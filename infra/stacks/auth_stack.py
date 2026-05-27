"""AuthStack: Cognito User Pool + App Client + Hosted UI + pre-created test user.

Phase 8 replaces the API-key gate with Cognito JWT auth. The Streamlit client logs in
via the hosted UI using `st.login()`; smoke/eval scripts use ADMIN_USER_PASSWORD_AUTH
through the same App Client.

The test user is created with `MessageAction=SUPPRESS` (no welcome email) and a permanent
password is set via an AwsCustomResource calling AdminSetUserPassword. The password value
lives in a Secrets Manager secret generated at deploy time (never in the CFN template).

References:
  https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_cognito-readme.html
  https://docs.aws.amazon.com/cognitoidentityprovider/latest/APIReference/API_AdminSetUserPassword.html
"""

from __future__ import annotations

import textwrap

from aws_cdk import CfnOutput, CustomResource, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

_TEST_USERNAME = "demo"

# Phase 12 — both local-dev and Streamlit Community Cloud URLs are registered.
# Cognito accepts an array of callback/logout URLs and the App Client picks the
# one matching the `redirect_uri` query param at login time, so keeping the
# localhost entry has no security cost and lets `./scripts/run_streamlit.sh`
# work against the same prod stacks.
_LOCAL_CALLBACK_URL = "http://localhost:8501/oauth2callback"
_LOCAL_LOGOUT_URL = "http://localhost:8501"
_PROD_CALLBACK_URL = "https://rag-aws-kb.streamlit.app/oauth2callback"
_PROD_LOGOUT_URL = "https://rag-aws-kb.streamlit.app"


class AuthStack(Stack):
    """Cognito User Pool, App Client (with secret), Hosted UI, and one test user."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="rag-aws-users",
            self_sign_up_enabled=False, # Random people cannot just visit the site and create an account; an administrator must invite or create them.
            sign_in_aliases=cognito.SignInAliases(username=True, email=False),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            mfa=cognito.Mfa.OFF,
            account_recovery=cognito.AccountRecovery.NONE,
            removal_policy=RemovalPolicy.DESTROY,
        )

        user_pool_client = user_pool.add_client(
            "AppClient",
            user_pool_client_name="rag-aws-streamlit",
            generate_secret=True,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                admin_user_password=True,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[_LOCAL_CALLBACK_URL, _PROD_CALLBACK_URL],
                logout_urls=[_LOCAL_LOGOUT_URL, _PROD_LOGOUT_URL],
            ),
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )

        # Cognito rejects domain prefixes containing "aws", "amazon", or "cognito".
        domain_prefix = f"ragkb-{self.account[-6:]}".lower()
        user_pool_domain = user_pool.add_domain(
            "HostedDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )

        # --- Test user (suppress email; password set via AdminSetUserPassword) ---
        test_password_secret = secretsmanager.Secret(
            self,
            "TestUserPassword",
            description="Permanent password for the Cognito test user (Phase 8 demo).",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=16,
                # Keep symbols (Cognito policy requires one); only exclude characters
                # that break shell/JSON quoting in smoke + eval scripts.
                exclude_characters='"\'`@/\\:; ',
                require_each_included_type=True,
                include_space=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        test_user = cognito.CfnUserPoolUser(
            self,
            "DemoUser",
            user_pool_id=user_pool.user_pool_id,
            username=_TEST_USERNAME,
            message_action="SUPPRESS", # No welcome email is sent to the test user.
            user_attributes=[
                cognito.CfnUserPoolUser.AttributeTypeProperty(
                    name="email", value="demo@aws-kb.example",
                ),
            ],
        )

        # Lambda-backed custom resource that reads the password from Secrets Manager
        # at runtime + calls AdminSetUserPassword. We cannot use the simpler
        # AwsCustomResource pattern with `secret_value.unsafe_unwrap()` because CFN
        # explicitly does NOT resolve `secretsmanager` dynamic references inside
        # Custom::AWS resource properties — the literal `{{resolve:...}}` token gets
        # passed verbatim to AdminSetUserPassword as the password.
        # https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/dynamic-references-secretsmanager.html
        # The Lambda reads the secret itself via boto3, so the actual value lands on
        # the user. Re-fires on every deploy where `SecretArn` changes — i.e., after
        # `cdk destroy --all` (secret regenerated → new ARN suffix → property diff →
        # CFN sends RequestType=Update or Create → password re-synced).
        set_password_fn = _lambda.Function(
            self,
            "SetPasswordFn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.seconds(30),
            log_retention=logs.RetentionDays.ONE_WEEK,
            code=_lambda.Code.from_inline(
                textwrap.dedent(
                    """
                    import json
                    import urllib.request
                    import boto3

                    sm = boto3.client("secretsmanager")
                    cognito = boto3.client("cognito-idp")

                    def handler(event, _ctx):
                        try:
                            if event["RequestType"] == "Delete":
                                return _send(event, "SUCCESS")
                            props = event["ResourceProperties"]
                            value = sm.get_secret_value(SecretId=props["SecretArn"])["SecretString"]
                            cognito.admin_set_user_password(
                                UserPoolId=props["UserPoolId"],
                                Username=props["Username"],
                                Password=value,
                                Permanent=True,
                            )
                            return _send(event, "SUCCESS", {"Synced": "true"})
                        except Exception as exc:
                            return _send(event, "FAILED", reason=str(exc))

                    def _send(event, status, data=None, reason=None):
                        body = json.dumps({
                            "Status": status,
                            "Reason": reason or "OK",
                            "PhysicalResourceId": event.get("PhysicalResourceId")
                                or event["ResourceProperties"]["SecretArn"],
                            "StackId": event["StackId"],
                            "RequestId": event["RequestId"],
                            "LogicalResourceId": event["LogicalResourceId"],
                            "Data": data or {},
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            event["ResponseURL"], data=body, method="PUT",
                        )
                        req.add_header("Content-Type", "")
                        req.add_header("Content-Length", str(len(body)))
                        urllib.request.urlopen(req)
                    """
                ).strip()
            ),
        )
        test_password_secret.grant_read(set_password_fn)
        set_password_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["cognito-idp:AdminSetUserPassword"],
                resources=[user_pool.user_pool_arn],
            )
        )

        set_password = CustomResource(
            self,
            "SetTestUserPassword",
            service_token=set_password_fn.function_arn,
            properties={
                # CFN sees a property diff whenever the secret is regenerated
                # (new ARN suffix), so the resource fires Update + the password
                # is re-synced. Same secret across an UPDATE = no diff = no-op.
                "SecretArn": test_password_secret.secret_arn,
                "UserPoolId": user_pool.user_pool_id,
                "Username": _TEST_USERNAME,
                "ForceUpdateTrigger": "run-number-1",
            },
        )
        set_password.node.add_dependency(test_user)
        set_password.node.add_dependency(test_password_secret)

        # --- Cognito-generated App Client secret, persisted for the Streamlit client ---
        # The client secret is created by Cognito (not by CDK), so the only way to expose
        # it as an output without leaking is to copy it into a Secrets Manager secret.
        # `user_pool_client_secret` is a SecretValue; unsafe_unwrap() yields a CFN dynamic
        # reference, not the literal value.
        client_secret_store = secretsmanager.Secret(
            self,
            "UserPoolClientSecret",
            description="Cognito App Client secret (consumed by the Streamlit client).",
            secret_string_value=user_pool_client.user_pool_client_secret,
            removal_policy=RemovalPolicy.DESTROY,
        )

        oauth_discovery_url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{user_pool.user_pool_id}/.well-known/openid-configuration"
        )

        # --- Outputs ----------------------------------------------------------
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolArn", value=user_pool.user_pool_arn)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(self, "UserPoolDomain", value=user_pool_domain.domain_name)
        CfnOutput(self, "OAuthDiscoveryUrl", value=oauth_discovery_url)
        CfnOutput(self, "TestUserName", value=_TEST_USERNAME)
        CfnOutput(
            self,
            "TestUserPasswordSecretArn",
            value=test_password_secret.secret_arn,
        )
        CfnOutput(
            self,
            "UserPoolClientSecretArn",
            value=client_secret_store.secret_arn,
        )

        self.user_pool = user_pool
        self.user_pool_client = user_pool_client
        self.user_pool_domain = user_pool_domain
        self.test_password_secret = test_password_secret
        self.client_secret_store = client_secret_store

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

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import custom_resources as cr
from constructs import Construct

_TEST_USERNAME = "demo"
_CALLBACK_URL = "http://localhost:8501/oauth2callback"
_LOGOUT_URL = "http://localhost:8501"


class AuthStack(Stack):
    """Cognito User Pool, App Client (with secret), Hosted UI, and one test user."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="rag-aws-users",
            self_sign_up_enabled=False,
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
                callback_urls=[_CALLBACK_URL],
                logout_urls=[_LOGOUT_URL],
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
            "TestUser",
            user_pool_id=user_pool.user_pool_id,
            username=_TEST_USERNAME,
            message_action="SUPPRESS",
            user_attributes=[
                cognito.CfnUserPoolUser.AttributeTypeProperty(
                    name="email", value="demo@acmenotes.example",
                ),
            ],
        )

        set_password = cr.AwsCustomResource(
            self,
            "SetTestUserPassword",
            on_create=cr.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminSetUserPassword",
                parameters={
                    "UserPoolId": user_pool.user_pool_id,
                    "Username": _TEST_USERNAME,
                    "Password": test_password_secret.secret_value.unsafe_unwrap(),
                    "Permanent": True,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"{construct_id}-test-user-password"
                ),
            ),
            on_update=cr.AwsSdkCall(
                service="CognitoIdentityServiceProvider",
                action="adminSetUserPassword",
                parameters={
                    "UserPoolId": user_pool.user_pool_id,
                    "Username": _TEST_USERNAME,
                    "Password": test_password_secret.secret_value.unsafe_unwrap(),
                    "Permanent": True,
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    f"{construct_id}-test-user-password"
                ),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["cognito-idp:AdminSetUserPassword"],
                        resources=[user_pool.user_pool_arn],
                    ),
                ]
            ),
            install_latest_aws_sdk=False,
        )
        set_password.node.add_dependency(test_user)

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

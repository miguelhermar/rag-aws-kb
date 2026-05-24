"""
AWS S3 service for document storage.
"""

import boto3
from botocore.exceptions import ClientError
from typing import Optional, BinaryIO
import logging
from pathlib import Path
import uuid
from datetime import datetime

from core.config import settings

logger = logging.getLogger(__name__)


class S3Service:
    """Service for interacting with AWS S3."""
    
    def __init__(self):
        """Initialize S3 client."""
        if not all([settings.AWS_ACCESS_KEY_ID, settings.AWS_SECRET_ACCESS_KEY, settings.S3_BUCKET_NAME]):
            logger.warning("S3 credentials not configured. File uploads will fail.")
            self.client = None
            return
        
        try:
            s3_config = {
                "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
                "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
                "region_name": settings.AWS_REGION
            }
            
            if settings.S3_ENDPOINT_URL:
                s3_config["endpoint_url"] = settings.S3_ENDPOINT_URL
            
            self.client = boto3.client("s3", **s3_config)
            self.bucket_name = settings.S3_BUCKET_NAME
            
            # Verify bucket exists
            self._ensure_bucket_exists()
            
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {str(e)}")
            self.client = None
    
    def _ensure_bucket_exists(self):
        """Ensure the S3 bucket exists, create if it doesn't."""
        if not self.client:
            return
        
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"S3 bucket '{self.bucket_name}' exists")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                # Bucket doesn't exist, try to create it
                try:
                    self.client.create_bucket(Bucket=self.bucket_name)
                    logger.info(f"Created S3 bucket '{self.bucket_name}'")
                except Exception as create_error:
                    logger.error(f"Failed to create S3 bucket: {str(create_error)}")
            else:
                logger.error(f"Error checking S3 bucket: {str(e)}")
    
    def upload_file(self, file_content: bytes, filename: str, content_type: str = "application/octet-stream") -> Optional[str]:
        """
        Upload a file to S3.
        
        Args:
            file_content: File content as bytes
            filename: Original filename
            content_type: MIME type of the file
            
        Returns:
            S3 key (path) if successful, None otherwise
        """
        if not self.client:
            raise ValueError("S3 client not initialized. Check AWS credentials.")
        
        # Generate unique S3 key
        file_ext = Path(filename).suffix
        unique_id = uuid.uuid4().hex[:8]
        timestamp = datetime.utcnow().strftime("%Y/%m/%d")
        s3_key = f"documents/{timestamp}/{unique_id}{file_ext}"
        
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType=content_type,
                Metadata={
                    "original_filename": filename,
                    "uploaded_at": datetime.utcnow().isoformat()
                }
            )
            
            logger.info(f"Uploaded file to S3: {s3_key}")
            return s3_key
            
        except ClientError as e:
            logger.error(f"Failed to upload file to S3: {str(e)}")
            raise
    
    def download_file(self, s3_key: str) -> Optional[bytes]:
        """
        Download a file from S3.
        
        Args:
            s3_key: S3 key (path) of the file
            
        Returns:
            File content as bytes, None if error
        """
        if not self.client:
            raise ValueError("S3 client not initialized.")
        
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=s3_key)
            return response["Body"].read()
            
        except ClientError as e:
            logger.error(f"Failed to download file from S3: {str(e)}")
            return None
    
    def delete_file(self, s3_key: str) -> bool:
        """
        Delete a file from S3.
        
        Args:
            s3_key: S3 key (path) of the file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.client:
            raise ValueError("S3 client not initialized.")
        
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.info(f"Deleted file from S3: {s3_key}")
            return True
            
        except ClientError as e:
            logger.error(f"Failed to delete file from S3: {str(e)}")
            return False
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600) -> Optional[str]:
        """
        Generate a presigned URL for temporary file access.
        
        Args:
            s3_key: S3 key (path) of the file
            expiration: URL expiration time in seconds (default 1 hour)
            
        Returns:
            Presigned URL string, None if error
        """
        if not self.client:
            return None
        
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": s3_key},
                ExpiresIn=expiration
            )
            return url
            
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {str(e)}")
            return None
    
    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3."""
        if not self.client:
            return False
        
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError:
            return False


# Global S3 service instance
s3_service = S3Service()


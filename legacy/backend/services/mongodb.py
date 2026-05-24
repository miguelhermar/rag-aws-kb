"""
MongoDB service for metadata storage.
"""

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
from typing import Optional, Dict, List
import logging
from datetime import datetime
from bson import ObjectId

from core.config import settings

logger = logging.getLogger(__name__)


class MongoDBService:
    """Service for interacting with MongoDB."""
    
    def __init__(self):
        """Initialize MongoDB client."""
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        
        if not settings.MONGODB_URI:
            logger.warning("MongoDB URI not configured. Metadata storage will be unavailable.")
            return
        
        try:
            self.client = AsyncIOMotorClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=5000
            )
            self.db = self.client[settings.MONGODB_DB_NAME]
            logger.info(f"Connected to MongoDB: {settings.MONGODB_DB_NAME}")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            self.client = None
    
    async def test_connection(self) -> bool:
        """Test MongoDB connection."""
        if not self.client:
            return False
        
        try:
            await self.client.admin.command("ping")
            return True
        except ConnectionFailure:
            return False
    
    async def insert_file_metadata(self, file_data: Dict) -> Optional[str]:
        """
        Insert file metadata into MongoDB.
        
        Args:
            file_data: Dictionary with file information
            
        Returns:
            Inserted document ID, None if error
        """
        if not self.db:
            return None
        
        try:
            file_data["created_at"] = datetime.utcnow()
            file_data["updated_at"] = datetime.utcnow()
            
            result = await self.db.files.insert_one(file_data)
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"Failed to insert file metadata: {str(e)}")
            return None
    
    async def get_file_metadata(self, file_id: str) -> Optional[Dict]:
        """Get file metadata by ID."""
        if not self.db:
            return None
        
        try:
            file_doc = await self.db.files.find_one({"_id": ObjectId(file_id)})
            if file_doc:
                file_doc["_id"] = str(file_doc["_id"])
            return file_doc
            
        except Exception as e:
            logger.error(f"Failed to get file metadata: {str(e)}")
            return None
    
    async def get_file_by_s3_key(self, s3_key: str) -> Optional[Dict]:
        """Get file metadata by S3 key."""
        if not self.db:
            return None
        
        try:
            file_doc = await self.db.files.find_one({"s3_key": s3_key})
            if file_doc:
                file_doc["_id"] = str(file_doc["_id"])
            return file_doc
            
        except Exception as e:
            logger.error(f"Failed to get file by S3 key: {str(e)}")
            return None
    
    async def list_files(self, skip: int = 0, limit: int = 100) -> List[Dict]:
        """List all files with pagination."""
        if not self.db:
            return []
        
        try:
            cursor = self.db.files.find().sort("created_at", -1).skip(skip).limit(limit)
            files = await cursor.to_list(length=limit)
            
            for file in files:
                file["_id"] = str(file["_id"])
            
            return files
            
        except Exception as e:
            logger.error(f"Failed to list files: {str(e)}")
            return []
    
    async def update_file_metadata(self, file_id: str, update_data: Dict) -> bool:
        """Update file metadata."""
        if not self.db:
            return False
        
        try:
            update_data["updated_at"] = datetime.utcnow()
            result = await self.db.files.update_one(
                {"_id": ObjectId(file_id)},
                {"$set": update_data}
            )
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Failed to update file metadata: {str(e)}")
            return False
    
    async def delete_file_metadata(self, file_id: str) -> bool:
        """Delete file metadata."""
        if not self.db:
            return False
        
        try:
            result = await self.db.files.delete_one({"_id": ObjectId(file_id)})
            return result.deleted_count > 0
            
        except Exception as e:
            logger.error(f"Failed to delete file metadata: {str(e)}")
            return False
    
    async def insert_chat_log(self, chat_data: Dict) -> Optional[str]:
        """Insert chat log entry."""
        if not self.db:
            return None
        
        try:
            chat_data["created_at"] = datetime.utcnow()
            result = await self.db.chat_logs.insert_one(chat_data)
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error(f"Failed to insert chat log: {str(e)}")
            return None
    
    async def get_chat_history(self, user_id: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get chat history."""
        if not self.db:
            return []
        
        try:
            query = {}
            if user_id:
                query["user_id"] = user_id
            
            cursor = self.db.chat_logs.find(query).sort("created_at", -1).limit(limit)
            chats = await cursor.to_list(length=limit)
            
            for chat in chats:
                chat["_id"] = str(chat["_id"])
            
            return chats
            
        except Exception as e:
            logger.error(f"Failed to get chat history: {str(e)}")
            return []


# Global MongoDB service instance
mongodb_service = MongoDBService()


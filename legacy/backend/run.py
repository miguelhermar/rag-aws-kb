"""
Alternative entry point for running the application.
Use this for better Windows compatibility.
"""

import uvicorn
import sys
from core.config import settings

if __name__ == "__main__":
    # Use 127.0.0.1 for Windows, 0.0.0.0 for Linux/Docker
    host = "127.0.0.1" if sys.platform == "win32" else "0.0.0.0"
    
    print(f"🚀 Starting server on http://{host}:8000")
    print(f"📚 API docs available at http://{host}:8000/docs")
    print(f"🌐 Environment: {settings.ENVIRONMENT}")
    
    try:
        uvicorn.run(
            "app:app",
            host=host,
            port=8000,
            reload=settings.ENVIRONMENT == "development",
            log_level="info",
            access_log=True
        )
    except OSError as e:
        if "address already in use" in str(e).lower() or "address already in use" in str(e):
            print(f"❌ Port 8000 is already in use!")
            print(f"💡 Try: netstat -ano | findstr :8000")
            print(f"💡 Or use a different port: uvicorn app:app --port 8001")
        else:
            print(f"❌ Error starting server: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
        sys.exit(0)


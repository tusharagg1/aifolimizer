"""Entry point for the FastAPI backend.

Run as a persistent service (scheduled task / boot): reload OFF so it is a
single robust process. For dev hot-reload use `uvicorn main:app --reload`
or set BACKEND_RELOAD=1.
"""

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=os.getenv("BACKEND_RELOAD", "0") == "1",
    )

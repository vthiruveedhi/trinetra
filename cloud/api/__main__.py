"""python -m cloud.api"""

import uvicorn
from cloud.api.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="0.0.0.0", port=18080, log_level="info")


if __name__ == "__main__":
    main()

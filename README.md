# QTradingBot Server

This is the `QTradingBot` backend built with `FastAPI`

## Files

`server.py` - Contains the `FastAPI` server implementation
`database.py` - Contains database related code (e.g syncing tables, queries)
`workers.py` - Contains coroutines that would run as tasks
`qbot.py` - Contains the actual implementation of the `Q-Learning algorithm`

## Running

### Clone the repo

```bash
git clone https://github.com/DaNutmeg/qbot-server.git

cd qbot-server
```

### Setup environment variables

```bash
# Copy enviroment variable template
cp .env.example .env
```

#### Notes

* You have to have a `postgresql` database server running
* Make sure to edit you `.env` file and add the correct variables or else errors

### Get necessary dependencies

```bash
# Create python virtual environments
python -m venv .venv

# Activate the virtual environment (Linux)
source .venv/bin/activate 

# Install dependencies
pip install -r requirements.txt
```

### Run

```bash
uvicorn server:app
```
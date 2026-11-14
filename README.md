# NASA2026

## Project Structure

```text
├── docker-compose.yml
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
│
├── data/
│   ├── raw/
│   └── processed/
│
├── src/
│   ├── __init__.py
│   ├── database.py
│   ├── models.py
│   │
│   └── ingestion/
│       ├── __init__.py
│       └── psi_client.py
```

## Getting Started

### Step 1 — Create the Python environment

Assuming you're using your Mac:

```bash
python3 --version
```

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

### Step 2 — Run the dependencies file

Install the requirements:

```bash
pip install -r requirements.txt
```

### Step 3 — Install Docker CLI and Colima

If you use Homebrew, run:

```bash
brew install docker docker-compose colima
```

### Step 4 — Start the Docker Engine

```bash
colima start
```

```
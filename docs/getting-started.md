# Getting Started

Welcome to GARY. This guide will help you install and run the local cognitive assistant on your Mac.

## Path A: Quick Start (For Users)
If you want to use GARY immediately with default settings:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/jeppsontaylor/neverhuman.git
   cd neverhuman
   ```
2. **Run the installer:**
   ```bash
   bash install.sh
   ```
3. The installer will download necessary dependencies (Python, local TLS, Homebrew) and launch the web UI where you can download the local models.

## Path B: Local Development (For Engineers)
For those wanting to modify or extend GARY:

1. **Prerequisites**: Python 3.11+, Docker Desktop, Node.js (for frontend).
2. **Setup virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Run the core**:
   ```bash
   python gary/server.py
   ```
4. **Access the interface**: Open `https://localhost:8000`.

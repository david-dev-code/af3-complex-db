# AF3-Complex-DB 

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

AF3-Complex-DB is a (local) web server and database designed for the management, 
storage, and analysis of AlphaFold 3 predicted protein complexes and their interactions. 

It provides an intuitive web interface and a powerful Command Line Interface (CLI) to ingest raw AlphaFold 3 outputs, automatically calculate biophysical metrics, map sequences to UniProt, and query the resulting dataset efficiently.


---

## 🚀 QUICK START & INSTALLATION

AF3-Complex-DB comes with an interactive installer that copies the codebase to a centralized location, configures your environment, and sets up a global CLI wrapper.

**Prerequisites:**
- Python 3.10+
- Docker & Docker Compose installed and running.

**Installation Steps:**
1. Clone or download this repository to your machine.
2. Navigate to the downloaded folder, and run the installer:
   ```bash
   python3 install.py
   ```
3. Follow the interactive wizard. The installer will:
   - Ask for a target installation directory (e.g., `~/af3_database`).
   - Copy the application code to the new directory.
   - Generate the `.env` configuration and setup the web server.
   - Install a global CLI command (`af3-db`) on your system.
   - Build and start the Docker containers.

---

## 💻 USAGE: COMMAND LINE INTERFACE (CLI)

The `af3-db` CLI is your main admin-tool for interacting with the database. 

### 1. Server Management
Manage the lifecycle of your AF3-Complex-DB instance directly from anywhere in your terminal:

```bash
af3-db start      # Starts the database and web server
af3-db stop       # Stops the running containers
af3-db config     # Opens the .env file in your default editor 
```

**Viewing Logs:**
If you need to monitor the server or debug an issue, use the logs command:
```bash
af3-db logs             # Shows the last 100 lines of all logs
af3-db logs web -f      # Follows the Python backend logs live
af3-db logs db -n 500   # Shows the last 500 lines of the database logs
```

### 2. Uploading Data
The `upload-folder` command is the workhorse of AF3-Complex-DB. It recursively scans a directory for AlphaFold 3 outputs and `.zip`/`.tar` archives.

**Standard Upload:**
```bash
af3-db upload-folder /path/to/my_af3_results
```

**Upload to a Specific Collection:**
```bash
af3-db upload-folder /path/to/results --collection "Kinase Project Q3"
```

**Upload with Custom Chain Mapping (Regex):**
If your alphafold run-folders are named systematically (e.g., `P12345_Q98765`), you can tell the database to map specific chains to specific UniProt accessions using the `--pattern` argument:
```bash
af3-db upload-folder /path/to/results --pattern "{A}_{B}"
```

### 3. Database Maintenance
```bash
af3-db delete-complex AF-CP-00001           # Deletes a specific complex
af3-db delete-collection "Kinase Project"   # Deletes a collection
af3-db purge-db                             # Wipes database and storage
```

---

## 🌐 USAGE: WEB INTERFACE

After starting the server, open your web browser and navigate to the address provided at the end of the installation (e.g. `http://localhost:3000`).

**Home & Quick Search:**
- View the most recently uploaded complexes.
- Use the large search bar to instantly find complexes by Accession ID, UniProt ID, Gene Name, Protein Name, or Organism.

**Advanced Search:**
Navigate to the "Advanced Search" tab to perform highly granular queries:
- Filter by ipTM, pTM, or pLDDT score ranges using interactive sliders.
- Filter by Oligomeric State (Monomer, Homomer, Heteromer).
- **Chain Filters:** Add specific conditions for individual chains within a complex (e.g., "Chain A must match 'Human Kinase' AND have an ipTM > 0.8").

**Collections:**
- View aggregated statistics for your collections (Average scores, Total entries).
- Explore visual analytics, including Species Distribution and Scatter Plots.
- Download entire collections as bulk `.zip` files.

**Complex Details:**
Clicking on an Accession ID (e.g., `AF-CP-00012`) opens the detailed view:
- **Interactive 3D Viewer:** Inspect the `.cif` structure directly in the browser.
- **Metrics:** View biophysical properties and different interface scores.
- **Chain Mappings:** See exactly which UniProt entries and genes were mapped.

---

## 🛡️ LICENSE & DISCLAIMER

AF3-Complex-DB is an independent, open-source tool developed for research and analytical purposes. It is not affiliated with, endorsed by, or sponsored by Google DeepMind.

Please respect the respective licensing terms of AlphaFold 3 and UniProt when utilizing the predictions and metadata.

---

## 👨‍💻 AUTHORS & ACKNOWLEDGEMENTS

* **David Henne** – Full stack development (idea, database, backend & UI)
* **Ruslan Ibragimov** – Foldseek integration
* **Prof. Dr. Kay Hofmann** – Group head, model contributions and testing

*(Note: A scientific paper detailing the architecture and application of AF3-Complex-DB is currently in preparation.)*

**Contact / Issues:** If you encounter any bugs or have feature requests, please open an issue on the GitHub repository. Contributions, pull requests, and feedback are highly welcome!

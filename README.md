# Retirement Planner

A personalized, local-first retirement planning web application built with Python and Dash.

## Overview

This dashboard serves as a customized retirement planning platform, focusing on a highly specific financial profile (e.g., married, California resident, real estate owner, no kids/pension). It provides a robust, monthly-granularity projection engine that handles:
- Federal and California state tax estimation
- Social Security modeling
- Investment growth projections
- Withdrawal strategies

All data stays local on your machine, ensuring complete privacy.

## Tech Stack
- [Python](https://www.python.org/)
- [Dash](https://dash.plotly.com/) (Web framework)
- [Dash Bootstrap Components](https://dash-bootstrap-components.opensource.faculty.ai/) (Darkly theme & layout)
- [Plotly](https://plotly.com/python/) (Data visualization)
- Pandas & NumPy (Data processing)

## Getting Started

### Prerequisites

Make sure you have Python 3 installed. You can install the required dependencies using the `requirements.txt` file.

### Installation

1. Clone or download this repository.
2. Create and activate a virtual environment (recommended):
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```
3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Running the App

Start the Dash development server by running:

```bash
python app.py
```

The app will be accessible in your web browser at: [http://127.0.0.1:8050/](http://127.0.0.1:8050/)

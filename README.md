# Fantasy Football Trade Analyzer & Roster Hub

A Streamlit application that pulls live NFL fantasy football data to evaluate trades based on market values and Rest-of-Season (ROS) projections. 

This tool relies on data integration rather than AI to provide fantasy advice.

## Features
* *Live Roster Sync:** Integrates directly with the Sleeper API to pull live rosters, lineup settings, and league scoring rules.
* *Algorithmic Trade Evaluation:** Cross-references live market values (via the FantasyCalc API) with Sleeper's Rest-of-Season point projections.
* *Performance Optimization:** Uses data caching to minimize API calls and prevent rate-limiting.
* *Custom UI:** Uses Streamlit to generate player cards, team logos, and positional styling.

## Tech Stack
* *Frontend/Backend:** Python, Streamlit
* *Data Processing:** Pandas, JSON
* *]*Integrations:** REST APIs (Sleeper API, FantasyCalc API, ESPN Image CDN)

## How to Run Locally
1. Clone this repository.
2. Install the required dependencies: `pip install -r requirements.txt`
3. Run the application: `streamlit run app.py`
4. Paste your Sleeper League ID into the web interface to load your league.

import streamlit as st
import requests
import openai
import json

# 1. Page Config & Title
st.set_page_config(page_title="AI Trade Analyzer", layout="centered")
st.title("🏈 AI Fantasy Trade Analyzer")
st.write("Enter your trade details and let the AI analyze it based on your league's actual rosters!")

# 2. Settings (Sidebar)
st.sidebar.header("Settings")
openai_api_key = st.sidebar.text_input("OpenAI API Key", type="password")
openai.api_key = openai_api_key

# 3. Inputs
league_id = st.text_input("Sleeper League ID (Try '1048299834168057856' for a test)")
st.divider()

st.header("Evaluate Trade")
col1, col2 = st.columns(2)

with col1:
    team_a_gets = st.text_area("You Receive", placeholder="e.g., Justin Jefferson")
with col2:
    team_b_gets = st.text_area("You Trade Away", "e.g., CeeDee Lamb")

# 4. The Magic Engine
if st.button("Evaluate Trade with AI", type="primary"):
    if not openai_api_key:
        st.error("Please enter your OpenAI API key in the sidebar menu on the left.")
    elif not league_id:
        st.error("Please enter a Sleeper League ID.")
    else:
        with st.spinner("Pulling live league data and analyzing..."):
            try:
                # Get the rosters from Sleeper
                roster_url = f"https://api.sleeper.app/v1/league/{league_id}/rosters"
                roster_response = requests.get(roster_url, timeout=5)
                roster_data = roster_response.json()
                
                # Turn the roster data into text for the AI
                roster_context = json.dumps(roster_data)[:2000] # Limiting size to save you API tokens
                
                # Build the AI Prompt
                prompt = f"""
                You are an expert fantasy football analyst. 
                Evaluate this trade for a half-PPR league.
                
                Side A receives: {team_a_gets}
                Side B receives: {team_b_gets}
                
                Here is some raw background data on the league rosters:
                {roster_context}
                
                Give a 2-paragraph verdict on who wins the trade and why based on Rest of Season value. 
                Consider if it makes sense based on the teams' current roster construction.
                """
                
                # Ask OpenAI
                response = openai.chat.completions.create(
                    model="gpt-4o-mini", # Faster and cheaper model
                    messages=[{"role": "user", "content": prompt}]
                )
                
                # Show the result!
                st.success("Analysis Complete!")
                st.write(response.choices[0].message.content)
                
            except requests.exceptions.Timeout:
                st.error("Sleeper API is currently overloaded. Try again later.")
            except Exception as e:
                st.error(f"An error occurred: {e}")

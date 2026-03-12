import os
import requests
from dotenv import load_dotenv
from whatsapp_api_client_python import API

def main():
    load_dotenv()
    ID_INSTANCE = os.getenv('ID_INSTANCE')
    API_TOKEN_INSTANCE = os.getenv('API_TOKEN_INSTANCE')

    greenAPI = API.GreenApi(ID_INSTANCE, API_TOKEN_INSTANCE)
    
    # Update enableMessagesHistory
    payload = {
        "enableMessagesHistory": "yes"
    }
    
    print("Enabling message history...")
    set_url = f"{greenAPI.host}/waInstance{ID_INSTANCE}/setSettings/{API_TOKEN_INSTANCE}"
    post_resp = requests.post(set_url, json=payload)
    
    if post_resp.status_code == 200:
        print("Successfully updated settings.")
        print("Response:", post_resp.json())
        
        # Verify
        print("Verifying...")
        get_url = f"{greenAPI.host}/waInstance{ID_INSTANCE}/getSettings/{API_TOKEN_INSTANCE}"
        get_resp = requests.get(get_url)
        if get_resp.status_code == 200:
            settings = get_resp.json()
            print("Current enableMessagesHistory:", settings.get("enableMessagesHistory"))
    else:
        print("Failed to set settings:", post_resp.status_code, post_resp.text)

if __name__ == "__main__":
    main()

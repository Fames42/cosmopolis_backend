import os
import json
import requests
from dotenv import load_dotenv
from whatsapp_api_client_python import API

def main():
    load_dotenv()
    ID_INSTANCE = os.getenv('ID_INSTANCE')
    API_TOKEN_INSTANCE = os.getenv('API_TOKEN_INSTANCE')

    greenAPI = API.GreenApi(ID_INSTANCE, API_TOKEN_INSTANCE)
    
    print("Fetching chats list...")
    url = f"{greenAPI.host}/waInstance{ID_INSTANCE}/getChats/{API_TOKEN_INSTANCE}"
    
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            chats = resp.json()
            print(f"Found {len(chats)} chats.")
            
            with open("chats.json", "w", encoding="utf-8") as f:
                json.dump(chats, f, indent=4, ensure_ascii=False)
            print("Saved chats list to chats.json.")
            
            # Print a quick preview of the first few chats
            print("\nPreview of the first 5 chats:")
            for c in chats[:5]:
                print(f" - {c.get('id', 'Unknown id')} ({c.get('name', '')})")
                
            print("\nNow let's fetch history for the first chat...")
            if len(chats) > 0:
                chat_id = chats[0].get('id')
                if chat_id:
                    history_payload = {"chatId": chat_id, "count": 10}
                    history_url = f"{greenAPI.host}/waInstance{ID_INSTANCE}/getChatHistory/{API_TOKEN_INSTANCE}"
                    hist_resp = requests.post(history_url, json=history_payload)
                    
                    if hist_resp.status_code == 200:
                        history = hist_resp.json()
                        print(f"Fetched {len(history)} messages for chat {chat_id}")
                        with open("history_preview.json", "w", encoding="utf-8") as f:
                            json.dump(history, f, indent=4, ensure_ascii=False)
                        print("Saved history preview to history_preview.json.")
                    else:
                        print("Failed to get chat history:", hist_resp.status_code, hist_resp.text)
        else:
            print(f"Failed to get chats. Status code: {resp.status_code}")
            print(resp.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()

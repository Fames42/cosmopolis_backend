import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

def main():
    load_dotenv()
    ID_INSTANCE = os.getenv('ID_INSTANCE')
    API_TOKEN_INSTANCE = os.getenv('API_TOKEN_INSTANCE')
    HOST = "https://7103.api.greenapi.com" # Using hardcoded host or could use SDK

    with open('chats.json', 'r', encoding='utf-8') as f:
        chats = json.load(f)

    if not os.path.exists('conversations'):
        os.makedirs('conversations')

    print(f"Loaded {len(chats)} chats. Starting extraction...")

    for i, chat in enumerate(chats):
        chat_id = chat.get('id')
        chat_name = chat.get('name', 'Unknown')
        # sanitize name for filename
        safe_name = "".join(x for x in chat_name if x.isalnum() or x in " -_").strip()
        filename = f"conversations/{chat_id}_{safe_name}.txt"
        
        url = f"{HOST}/waInstance{ID_INSTANCE}/getChatHistory/{API_TOKEN_INSTANCE}"
        payload = {"chatId": chat_id, "count": 1000} # Get up to 1000 recent messages
        
        try:
            resp = requests.post(url, json=payload)
            if resp.status_code == 200:
                history = resp.json()
                if not history:
                    print(f"[{i+1}/{len(chats)}] No history for {chat_name} ({chat_id})")
                    continue
                
                # Sort messages chronologically (oldest first)
                # Green API returns chronological or reverse? It usually returns reverse chronological. 
                # Let's sort by timestamp ascending.
                history.sort(key=lambda x: x.get('timestamp', 0))
                
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"Conversation with {chat_name} ({chat_id})\n")
                    f.write("="*40 + "\n\n")
                    
                    for msg in history:
                        timestamp = msg.get('timestamp', 0)
                        dt = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else "Unknown Time"
                        
                        sender = msg.get('senderName') or msg.get('senderId', 'Unknown Sender')
                        if msg.get('type') == 'outgoing':
                            sender = 'Me'
                        
                        text = "..."
                        msg_type = msg.get('typeMessage')
                        if msg_type == 'textMessage':
                            text = msg.get('textMessage', '')
                        elif msg_type == 'extendedTextMessage':
                            text = msg.get('extendedTextMessage', {}).get('text', '')
                        elif msg_type == 'imageMessage':
                            text = "[Image]"
                        elif msg_type == 'documentMessage':
                            text = "[Document]"
                        elif msg_type == 'audioMessage':
                            text = "[Audio]"
                        else:
                            text = f"[{msg_type}]"
                            
                        f.write(f"[{dt}] {sender}:\n{text}\n\n")
                
                print(f"[{i+1}/{len(chats)}] Saved {len(history)} messages for {chat_name} ({chat_id})")
            else:
                print(f"[{i+1}/{len(chats)}] Failed to fetch history for {chat_id}: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"[{i+1}/{len(chats)}] Error fetching for {chat_id}: {e}")

if __name__ == "__main__":
    main()

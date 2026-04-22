from pymongo import MongoClient
client = MongoClient('mongodb://localhost:27017/')
db = client['pepper_chat_db']
chat = list(db.chats.find({'messages.content': {'$regex': 'CONFIRM_GMAIL'}}).sort('updated_at', -1).limit(1))
if chat:
    for m in chat[0].get('messages', [])[-6:]:
        print(m.get('role'), ':', str(m.get('content', ''))[:100].replace('\n', ' '))
else:
    print('No chats found.')

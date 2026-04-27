from pymongo import MongoClient
from config_loader import cfg

client = MongoClient(cfg.mongo_uri)
db = client[cfg.mongo_database]
chat = list(db.chats.find({'messages.content': {'$regex': 'CONFIRM_GMAIL'}}).sort('updated_at', -1).limit(1))
if chat:
    for m in chat[0].get('messages', [])[-6:]:
        print(m.get('role'), ':', str(m.get('content', ''))[:100].replace('\n', ' '))
else:
    print('No chats found.')

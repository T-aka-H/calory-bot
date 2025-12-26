import os
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from openai import OpenAI

# .envファイルを読み込む
load_dotenv()

app = Flask(__name__)

# 環境変数から取得
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# LINE設定
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# OpenAI設定
openai_client = OpenAI(api_key=OPENAI_API_KEY)

def get_calorie_info(food_name: str) -> str:
    """ChatGPTにカロリー情報を問い合わせ"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
            messages=[
                {
                    "role": "system",
                    "content": """あなたは置き換えダイエットの専門家です。
ユーザーが食材や料理名を送ってきたら、以下の形式で回答してください。

1. その食品のカロリー（一般的な1人前）
2. 置き換えアドバイス：食材や調理法を変えることでカロリーを抑える具体的な提案を2〜3個

回答は150〜200文字程度で簡潔にまとめてください。
親しみやすい口調で答えてください。"""
                },
                {
                    "role": "user",
                    "content": f"「{food_name}」のカロリーと、カロリーを抑える方法を教えて"
                }
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"エラーが発生しました: {str(e)}"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    food_name = event.message.text
    calorie_info = get_calorie_info(food_name)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=calorie_info)]
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
```

---

### 2. `requirements.txt`
```
flask
line-bot-sdk
openai
python-dotenv
gunicorn
```

---

### 3. `.env`（ローカル用・Gitにあげない）
```
LINE_CHANNEL_SECRET=ここにチャネルシークレット
LINE_CHANNEL_ACCESS_TOKEN=ここにアクセストークン
OPENAI_API_KEY=ここにOpenAIのAPIキー
```

---

### 4. `.gitignore`
```
.env
venv/
__pycache__/
```

---

## フォルダ構成
```
calorie-bot/
├── app.py
├── requirements.txt
├── .env
└── .gitignore

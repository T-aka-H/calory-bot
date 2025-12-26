import os
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from openai import OpenAI

# .envファイルを読み込む
load_dotenv()

app = Flask(__name__)

# タイムゾーン設定
JST = timezone(timedelta(hours=9))

# ログファイルのパス
LOG_FILE = "message_log.json"

# 環境変数から取得
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')  # 管理者のLINEユーザーID

# LINE設定
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# OpenAI設定
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def load_log():
    """ログファイルを読み込む"""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def save_log(log_data):
    """ログファイルに保存"""
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)


def add_log_entry(user_name, user_id, message):
    """ログエントリを追加"""
    log = load_log()
    timestamp = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    log.append({
        "timestamp": timestamp,
        "user_name": user_name,
        "user_id": user_id,
        "message": message
    })
    save_log(log)
    print(f"[{timestamp}] {user_name}: {message}")


def get_daily_summary():
    """本日の利用状況サマリーを生成"""
    log = load_log()
    today = datetime.now(JST).strftime("%Y-%m-%d")

    # 本日のログのみ抽出
    today_logs = [entry for entry in log if entry["timestamp"].startswith(today)]

    if not today_logs:
        return "本日の利用はありませんでした。"

    # 統計情報
    total_count = len(today_logs)
    users = set(entry["user_name"] for entry in today_logs)
    user_count = len(users)

    # 人気の食材（上位3件）
    foods = [entry["message"] for entry in today_logs]
    food_counts = {}
    for food in foods:
        food_counts[food] = food_counts.get(food, 0) + 1
    popular_foods = sorted(food_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    popular_text = "、".join([f"{food}({count}件)" for food, count in popular_foods])

    return (
        "📊 本日の利用状況\n\n"
        f"利用回数: {total_count}件\n"
        f"ユーザー数: {user_count}人\n"
        f"人気の食材: {popular_text}"
    )


def send_daily_summary():
    """管理者にサマリーを送信"""
    if not ADMIN_USER_ID:
        print("ADMIN_USER_IDが設定されていません")
        return

    summary = get_daily_summary()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=ADMIN_USER_ID,
                messages=[TextMessage(text=summary)]
            )
        )
    print("サマリーを送信しました")


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
ユーザーが食材の名前を送ってきたら、以下の形式で回答してください。

1. その食品のカロリー（一般的な1人前）
2. 置き換えアドバイス（食材の名前を変えることでカロリーを抑える具体的な提案を2つ）

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


@app.route("/")
def health():
    return "OK"


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@app.route("/summary", methods=['POST'])
def summary():
    """サマリー送信用エンドポイント（外部スケジューラーから呼び出す）"""
    send_daily_summary()
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    food_name = event.message.text

    # ユーザー名を取得
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        profile = line_bot_api.get_profile(user_id)
        user_name = profile.display_name

    # ログに記録
    add_log_entry(user_name, user_id, food_name)

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
    app.run(host='0.0.0.0', port=port, debug=False)

import os
import json
import random
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage,
    QuickReply, QuickReplyItem, MessageAction
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from openai import OpenAI
from supabase import create_client, Client

# .envファイルを読み込む
load_dotenv()

app = Flask(__name__)

# タイムゾーン設定
JST = timezone(timedelta(hours=9))

# 環境変数から取得
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# LINE設定
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# OpenAI設定
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Supabase設定
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===== ログ機能 =====
def add_log_entry(user_name, user_id, message):
    """ログをSupabaseに保存"""
    timestamp = datetime.now(JST).isoformat()
    supabase.table('message_logs').insert({
        'user_id': user_id,
        'user_name': user_name,
        'message': message
    }).execute()
    print(f"[{timestamp}] {user_name}: {message}")


def get_daily_summary():
    """本日の利用状況サマリーを生成"""
    today = datetime.now(JST).strftime("%Y-%m-%d")

    result = supabase.table('message_logs').select('*').gte('timestamp', today).execute()
    logs = result.data

    if not logs:
        return "本日の利用はありませんでした。"

    total_count = len(logs)
    users = set(entry['user_name'] for entry in logs)
    user_count = len(users)

    messages = [entry['message'] for entry in logs]
    message_counts = {}
    for msg in messages:
        message_counts[msg] = message_counts.get(msg, 0) + 1
    popular = sorted(message_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    popular_text = "、".join([f"{msg}({count}件)" for msg, count in popular])

    return f"📊 本日の利用状況\n\n利用回数: {total_count}件\nユーザー数: {user_count}人\n人気: {popular_text}"


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


# ===== カロリー機能 =====
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
        message = response.choices[0].message.content
        if not message.rstrip().endswith("Have a nice calory!"):
            message = f"{message.rstrip()}\n\nHave a nice calory!"
        return message
    except Exception as e:
        return f"エラーが発生しました: {str(e)}"


# ===== クイズ機能 =====
def get_user_progress(user_id):
    """ユーザーのクイズ進捗を取得"""
    result = supabase.table('quiz_progress').select('*').eq('user_id', user_id).execute()
    if result.data:
        return result.data[0]
    # 新規ユーザーの場合は作成
    supabase.table('quiz_progress').insert({
        'user_id': user_id,
        'current_quiz_id': 0,
        'correct_count': 0,
        'total_count': 0
    }).execute()
    return {'user_id': user_id, 'current_quiz_id': 0, 'correct_count': 0, 'total_count': 0}


def update_user_progress(user_id, quiz_id, is_correct):
    """ユーザーの進捗を更新"""
    progress = get_user_progress(user_id)
    new_correct = progress['correct_count'] + (1 if is_correct else 0)
    new_total = progress['total_count'] + 1

    supabase.table('quiz_progress').update({
        'current_quiz_id': quiz_id,
        'correct_count': new_correct,
        'total_count': new_total,
        'updated_at': datetime.now(JST).isoformat()
    }).eq('user_id', user_id).execute()


def get_quiz(quiz_id):
    """クイズを取得"""
    result = supabase.table('quizzes').select('*').eq('id', quiz_id).execute()
    if result.data:
        return result.data[0]
    return None


def get_random_quiz(user_id):
    """ユーザーがまだ解いていないクイズをランダムに取得"""
    # 全クイズを取得
    result = supabase.table('quizzes').select('*').execute()
    all_quizzes = result.data

    if not all_quizzes:
        # クイズがない場合はAIで生成
        return generate_quiz_with_ai()

    # ランダムに1問選択
    quiz = random.choice(all_quizzes)
    return quiz


def generate_quiz_with_ai():
    """AIで新しいクイズを生成"""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
            messages=[
                {
                    "role": "system",
                    "content": """カロリー置き換えダイエットに関する3択クイズを1問作成してください。
以下のJSON形式で出力してください：
{
    "question": "問題文",
    "choice_a": "選択肢A",
    "choice_b": "選択肢B", 
    "choice_c": "選択肢C",
    "correct_answer": "A",
    "explanation": "解説（100文字程度）"
}
正解はA, B, Cのいずれかで、ランダムに設定してください。
実用的で意外性のある豆知識を含めてください。"""
                }
            ]
        )
        quiz_data = json.loads(response.choices[0].message.content)

        # データベースに保存
        result = supabase.table('quizzes').insert(quiz_data).execute()
        return result.data[0]
    except Exception as e:
        print(f"クイズ生成エラー: {e}")
        return None


def format_quiz_question(quiz):
    """クイズを出題形式にフォーマット"""
    return f"""🎯 カロリークイズ

{quiz['question']}

A. {quiz['choice_a']}
B. {quiz['choice_b']}
C. {quiz['choice_c']}

→ A, B, C のどれかを送ってね"""


def check_answer(user_id, answer):
    """回答をチェック"""
    progress = get_user_progress(user_id)
    current_quiz_id = progress['current_quiz_id']

    if current_quiz_id == 0:
        return "まだクイズに挑戦していません。「#クイズ」と送ってね！", False

    quiz = get_quiz(current_quiz_id)
    if not quiz:
        return "クイズが見つかりませんでした。「#クイズ」で新しい問題に挑戦！", False

    answer = answer.upper()
    is_correct = (answer == quiz['correct_answer'])

    update_user_progress(user_id, 0, is_correct)  # quiz_idを0にリセット

    progress = get_user_progress(user_id)
    stats = f"\n\n📊 成績: {progress['correct_count']}/{progress['total_count']}問正解"

    if is_correct:
        return f"⭕ 正解！\n\n{quiz['explanation']}{stats}", True
    else:
        correct_text = quiz[f"choice_{quiz['correct_answer'].lower()}"]
        return f"❌ 残念！正解は {quiz['correct_answer']}. {correct_text}\n\n{quiz['explanation']}{stats}", True


def start_quiz(user_id):
    """クイズを開始"""
    quiz = get_random_quiz(user_id)

    if not quiz:
        return "クイズの準備ができませんでした。しばらくしてからお試しください。"

    # 現在のクイズIDを保存
    supabase.table('quiz_progress').update({
        'current_quiz_id': quiz['id'],
        'updated_at': datetime.now(JST).isoformat()
    }).eq('user_id', user_id).execute()

    return format_quiz_question(quiz)


# ===== 開発日記機能 =====
ARTICLES_FILE = "articles.json"

def load_articles():
    """記事データを読み込む"""
    if os.path.exists(ARTICLES_FILE):
        with open(ARTICLES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def get_article_list():
    """記事一覧を取得"""
    articles = load_articles()
    if not articles:
        return "まだ記事がありません。"

    latest = articles[-5:]
    reply_text = "📓 開発日記\n\n"
    for a in reversed(latest):
        reply_text += f"[{a['id']}] {a['date']}\n{a['title']}\n\n"
    reply_text += "→ 番号を送ると詳細が見れます"
    return reply_text


def get_article_detail(article_id):
    """記事詳細を取得"""
    articles = load_articles()
    article = next((a for a in articles if a['id'] == article_id), None)
    if article:
        return f"📝 {article['title']}\n📅 {article['date']}\n\n{article['content']}"
    return None


# ===== ルーティング =====
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
    """サマリー送信用エンドポイント"""
    send_daily_summary()
    return "OK"


@app.route("/health-db", methods=['GET'])
def health_db():
    """Supabase??????"""
    try:
        supabase.table('quizzes').select('id').limit(1).execute()
        return "OK"
    except Exception as e:
        return f"Error: {e}", 500


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    message_text = event.message.text

    # ユーザー名を取得
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        profile = line_bot_api.get_profile(user_id)
        user_name = profile.display_name

    # ログに記録
    add_log_entry(user_name, user_id, message_text)

    # クイックリプライを使うかどうかのフラグ
    show_quiz_quick_reply = False
    show_answer_quick_reply = False

    # メッセージの種類に応じて処理を分岐
    if message_text == "#クイズ":
        reply_text = start_quiz(user_id)
        show_answer_quick_reply = True

    elif message_text.upper() in ['A', 'B', 'C']:
        reply_text, show_quiz_quick_reply = check_answer(user_id, message_text)

    elif message_text == "#開発日記":
        reply_text = get_article_list()

    elif message_text == "#カロリー":
        reply_text = "🍽 カロリー検索\n\n気になる食材名を教えて！\n例: ラーメン、餃子、カレーライス"

    elif message_text.startswith("#記事"):
        try:
            article_id = int(message_text.replace("#記事", ""))
            article = get_article_detail(article_id)
            reply_text = article if article else "その記事は見つかりませんでした。"
        except ValueError:
            reply_text = "記事番号を指定してください。例: #記事1"

    elif message_text.isdigit():
        article_id = int(message_text)
        article = get_article_detail(article_id)
        if article:
            reply_text = article
        else:
            reply_text = get_calorie_info(message_text)

    else:
        reply_text = get_calorie_info(message_text)

    # 返信メッセージを作成
    if show_quiz_quick_reply:
        message = TextMessage(
            text=reply_text,
            quick_reply=QuickReply(items=[
                QuickReplyItem(action=MessageAction(label="次の問題へ", text="#クイズ")),
                QuickReplyItem(action=MessageAction(label="終了", text="#カロリー"))
            ])
        )
    elif show_answer_quick_reply:
        message = TextMessage(
            text=reply_text,
            quick_reply=QuickReply(items=[
                QuickReplyItem(action=MessageAction(label="A", text="A")),
                QuickReplyItem(action=MessageAction(label="B", text="B")),
                QuickReplyItem(action=MessageAction(label="C", text="C"))
            ])
        )
    else:
        message = TextMessage(text=reply_text)

    # 返信
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[message]
            )
        )


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

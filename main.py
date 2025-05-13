import os
import re
import pickle
import base64
import dotenv
from datetime import datetime, timedelta

from openai import OpenAI
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

dotenv.load_dotenv()

CREDENTIAL_FILENAME = 'credentials.json'  # Google Cloud Consoleで作成したOAuth2.0クライアントIDのJSONファイルのパス

# 必要な権限スコープを設定
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def authenticate_gmail():
    """Gmailへの認証を行い、サービスオブジェクトを返す"""
    creds = None
    
    # トークンの読み込み（存在する場合）
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    # 認証情報が存在しないか、無効な場合は新たに認証を行う
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIAL_FILENAME, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # 次回のために認証情報を保存
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return build('gmail', 'v1', credentials=creds)

def get_emails_by_subject(service, query, max_results=5):
    """
    指定した件名でフィルタリングし、最新のメールを取得する
    
    引数:
        service: Gmail API サービスオブジェクト
        subject_filter: 検索する件名（部分一致）
        max_results: 取得するメールの最大数
    
    戻り値:
        メールのリスト（新しい順）
    """
    # 件名で検索するクエリを作成
    # ２４時間以内のメールに限定する
    # query = f"subject:{filter_query} newer_than:1d"
    
    # メールリストを取得
    results = service.users().messages().list(
        userId='me',
        q=query,
        maxResults=max_results
    ).execute()
    
    messages = results.get('messages', [])
    
    if not messages:
        print("該当するメールが見つかりませんでした。")
        return []
    
    email_list = []
    
    # 各メールの詳細情報を取得
    for message in messages:
        msg = service.users().messages().get(
            userId='me',
            id=message['id'],
            format='full'
        ).execute()
        
        # ヘッダーから件名と送信者を取得
        headers = msg['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        
        # 日時をパース（RFC 2822形式からdatetimeオブジェクトへ）
        date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')
        try:
            # メールの日時形式はバリエーションがあるため、複数のパターンを試す
            date_formats = [
                '%a, %d %b %Y %H:%M:%S %z',
                '%a, %d %b %Y %H:%M:%S %z (%Z)',
                '%d %b %Y %H:%M:%S %z'
            ]
            
            date_obj = None
            for fmt in date_formats:
                try:
                    # タイムゾーン情報を含む部分を正規化
                    cleaned_date = re.sub(r'\([^)]*\)', '', date_str).strip()
                    date_obj = datetime.strptime(cleaned_date, fmt)
                    break
                except ValueError:
                    continue
            
            if date_obj is None:
                date_obj = datetime.now()  # パースに失敗した場合は現在時刻を使用
                
        except Exception as e:
            print(f"日付のパースに失敗: {e}")
            date_obj = datetime.now()
        
        # メッセージ本文を取得（プレーンテキストがあれば）
        body = ""
        if 'parts' in msg['payload']:
            for part in msg['payload']['parts']:
                if part['mimeType'] == 'text/plain':
                    body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                    break
        elif 'body' in msg['payload'] and 'data' in msg['payload']['body']:
            body = base64.urlsafe_b64decode(msg['payload']['body']['data']).decode('utf-8')
        
        # メール情報を辞書にまとめる
        email_data = {
            'id': message['id'],
            'subject': subject,
            'sender': sender,
            'date': date_obj,
            # 'body': body[:200] + ('...' if len(body) > 200 else '')  # 本文は最初の200文字まで
            'body': body,
        }
        
        email_list.append(email_data)
    
    # 日付の新しい順にソート
    email_list.sort(key=lambda x: x['date'], reverse=True)
    
    return email_list

def main(after_date: datetime, subject_filter: str):
    """メイン関数"""
    # Gmailに認証
    service = authenticate_gmail()  # credentials.jsonは事前にGoogle Cloud Consoleで作成しておく必要があります

    # フィルター条件を入力
    max_results = 30  # 取得するメールの最大数
    after_date_text = after_date.strftime(r'%Y/%m/%d')
    before_date_text = (after_date + timedelta(days=6)).strftime(r'%Y/%m/%d')
    query = f'subject:{subject_filter} after:{after_date_text} before:{before_date_text}'

    # メールを取得
    emails = get_emails_by_subject(service, query, max_results)

    # メールを整形して記事一覧にする
    urls = []
    articles = []
    for email in emails:
        body = email['body']
        arts = re.split('(===|- - - )', body)[4].split('\r\n\r\n')[1:-1]
        for a in arts:
            body = ''.join(re.split('\r\n', a)[:-1]).strip()
            url = re.split('\r\n', a)[-1].strip()
            url = re.sub(r'[<>]', '', url)
            url = re.sub('http.*http', 'http', url)
            url = re.sub('&ct=ga&.*', '', url)
            if url not in urls:
                articles.append({
                    'body': body,
                    'url': url,
                })
                urls.append(url)

    news_text = ''
    for article in articles:
        news_text += f"{article['body']}\n{article['url']}\n\n"

    after_date_removed_slash = after_date_text.replace('/', '')
    before_date_removed_slash = before_date_text.replace('/', '')
    output_filename = f'output_{after_date_removed_slash}-{before_date_removed_slash}.txt'
    news_text = f'''\
###### ニュースの期間

{after_date_text}～{before_date_text}

###### ニュース記事
{news_text}
'''

    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(news_text)

    # print(news_text)

    # 生成AIで記事を整形して無関係な記事を削除し重複した記事をまとめる
    prompt = f'''
与えられたニュース記事を以下のフォーマットで出力してください。

ニュースの期間：{after_date_text}～{before_date_text}

###### 制約
- ニュース記事は、AWS(Amazon Web Services)に関連するものである必要があります。
- 同じ内容の記事はまとめてください。.

###### 出力フォーマット
● 記事のタイトル - 記事の概要 記事のURL
● 記事のタイトル - 記事の概要 記事のURL

{news_text}
'''

    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY'),
        # organization=os.getenv('OPENAI_ORGANIZATION'),
    )
    response = client.responses.create(
        model="o4-mini",
        input=prompt,
        # temperature=0.0,
    )
    news_text_formatted = response.output_text

    # 結果をファイルに保存
    output_filename_formatted = output_filename.replace('.txt', '_formatted.txt')
    with open(output_filename_formatted, 'w', encoding='utf-8') as f:
        f.write(news_text_formatted)

    print(news_text_formatted)

    pass


if __name__ == "__main__":
    main(
        after_date=datetime(2025, 5, 5),
        # subject_filter='("Google アラート" -別用途のアラートがあれば -このようにメールサブジェクトで除外可能です)',
    )

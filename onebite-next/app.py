from flask import Flask, render_template, request, jsonify
import os
from werkzeug.utils import secure_filename
from datetime import datetime
import google.generativeai as genai
import json
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

# 데이터 파일 경로 설정 (절대 경로 사용)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DATA_FILE = os.path.join(DATA_DIR, 'posts.json')
POST_COUNTS_FILE = os.path.join(DATA_DIR, 'post_counts.json')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')

# 데이터 저장 폴더 생성
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Gemini API 설정
GOOGLE_API_KEY = 'AIzaSyCXeiAnEv3ou17DAqiwTmua4sVbNF6cG2A'
genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# 데이터 로드 함수
def load_data():
    global posts, post_counts
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                posts_data = json.load(f)
                posts = []
                for post in posts_data:
                    post['liked_by'] = set(post['liked_by'])  # list를 set으로 변환
                    posts.append(post)
        else:
            posts = []
            
        if os.path.exists(POST_COUNTS_FILE):
            with open(POST_COUNTS_FILE, 'r', encoding='utf-8') as f:
                post_counts = json.load(f)
        else:
            post_counts = {}
    except Exception as e:
        print('Data load error:', str(e))
        posts = []
        post_counts = {}

# 데이터 저장 함수
def save_data():
    try:
        # posts 저장 (set을 list로 변환)
        posts_to_save = []
        for post in posts:
            post_copy = post.copy()
            post_copy['liked_by'] = list(post['liked_by'])
            posts_to_save.append(post_copy)
            
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(posts_to_save, f, ensure_ascii=False, indent=2)
            
        # post_counts 저장
        with open(POST_COUNTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(post_counts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Data save error:', str(e))

# 초기 데이터 로드
load_data()

# 글작성 제한 횟수
POST_LIMIT = 3

@app.route('/')
def index():
    # set을 list로 변환하여 템플릿에 전달
    posts_for_template = []
    for post in posts:
        post_copy = post.copy()
        post_copy['liked_by'] = list(post['liked_by'])
        posts_for_template.append(post_copy)
    return render_template('index.html', posts=posts_for_template)

@app.route('/post/<int:post_id>', methods=['GET'])
def get_post(post_id):
    for post in posts:
        if post['id'] == post_id:
            # 조회수 증가
            post['views'] = post.get('views', 0) + 1
            # 데이터 저장
            save_data()
            response_post = post.copy()
            response_post['liked_by'] = list(post['liked_by'])
            return jsonify({'success': True, 'post': response_post})
    return jsonify({'error': '게시물을 찾을 수 없습니다'}), 404

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'image' not in request.files:
        return jsonify({'error': '이미지가 없습니다'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': '선택된 파일이 없습니다'}), 400
    
    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return jsonify({
            'success': True,
            'filename': filename,
            'url': f'/static/uploads/{filename}'
        })

def check_content(text):
    try:
        # 빈 텍스트 체크
        text = text.strip()
        if not text or len(text) < 2:
            return False, None

        # 1. 전체 텍스트 길이 제한 (1000자)
        if len(text) > 1000:
            return True, "도배 감지: 텍스트가 너무 깁니다 (1000자 제한)"

        # 2. 연속 문자 반복 검사
        def check_char_repeat(text):
            repeat_count = 1
            prev_char = text[0]
            for char in text[1:]:
                if char == prev_char:
                    repeat_count += 1
                    if repeat_count >= 2:
                        return True
                else:
                    repeat_count = 1
                prev_char = char
            return False

        if check_char_repeat(text):
            return True, "도배 감지: 동일한 문자를 과도하게 반복했습니다"

        # 3. 단어 반복 검사
        words = text.split()
        if len(words) >= 2:
            for i in range(len(words)-1):
                if words[i] == words[i+1]:
                    return True, "도배 감지: 동일한 단어를 연속해서 사용했습니다"

        # 4. 특수문자 검사
        special_chars = set('!@#$%^&*()_+-=[]{}|;:,.<>?~`₩"\'ㅋㅎㅠㅜㅡㅇㄷㅂㅅㅈㅊㅍㅌㄹㅁㄴㅣㅏㅓㅗㅜㅡㅢㅚㅐㅔ')
        special_count = sum(1 for c in text if c in special_chars)
        
        if len(text) > 0 and special_count / len(text) > 0.2:
            return True, "도배 감지: 특수문자나 이모티콘을 과도하게 사용했습니다"

        # 5. AI 판단
        prompt = f"""다음 텍스트에 욕설이나 도배가 있는지 확인해주세요: {text}
        
        확인할 내용:
        1. 욕설/비속어
        2. 비난/혐오 표현
        3. 도배성 텍스트
        
        발견된 문제점을 다음 형식으로만 답변해주세요:
        - 욕설/비속어가 있으면: "욕설: true"
        - 도배성 텍스트면: "도배: true"
        - 문제없으면: "false"
        """
        
        response = model.generate_content(prompt)
        
        if not response or not hasattr(response, 'text'):
            return True, "내용 검증 중 오류가 발생했습니다"
            
        result = response.text.lower().strip()
        if '욕설: true' in result:
            return True, "부적절한 내용 감지: 욕설이나 비속어가 포함되어 있습니다"
        elif '도배: true' in result:
            return True, "도배 감지: AI가 도배성 텍스트로 판단했습니다"
            
        return False, None
        
    except Exception as e:
        print('Content check error:', str(e))
        return True, "내용 검증 중 오류가 발생했습니다"

@app.route('/post', methods=['POST'])
def create_post():
    try:
        data = request.json
        if not data:
            return jsonify({'error': '요청 데이터가 없습니다'}), 400
            
        user_ip = request.remote_addr
        
        if user_ip in post_counts:
            if post_counts[user_ip] >= POST_LIMIT:
                return jsonify({'error': f'하루 {POST_LIMIT}회 이상 글을 작성할 수 없습니다'}), 400
            post_counts[user_ip] += 1
        else:
            post_counts[user_ip] = 1
            
        content = data.get('content', '').strip()
        
        if not content:
            return jsonify({'error': '내용을 입력해주세요'}), 400
        
        is_invalid, error_message = check_content(content)
        if is_invalid:
            return jsonify({'error': error_message}), 400
        
        post = {
            'id': len(posts) + 1,
            'content': content,
            'image_url': data.get('image_url'),
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'likes': 0,
            'views': 0,
            'liked_by': set(),
            'comments': [],
            'user_ip': user_ip
        }
        posts.insert(0, post)
        
        # 데이터 저장
        save_data()
        
        response_post = post.copy()
        response_post['liked_by'] = list(post['liked_by'])
        return jsonify({'success': True, 'post': response_post})
        
    except Exception as e:
        print('Create post error:', str(e))
        return jsonify({'error': '게시물 작성 중 오류가 발생했습니다'}), 500

@app.route('/like/<int:post_id>', methods=['POST'])
def like_post(post_id):
    for post in posts:
        if post['id'] == post_id:
            user_id = request.remote_addr
            if user_id in post['liked_by']:
                post['liked_by'].remove(user_id)
                post['likes'] -= 1
                liked = False
            else:
                post['liked_by'].add(user_id)
                post['likes'] += 1
                liked = True
            # 데이터 저장
            save_data()
            return jsonify({
                'success': True,
                'likes': post['likes'],
                'liked': liked
            })
    return jsonify({'error': '게시물을 찾을 수 없습니다'}), 404

@app.route('/comment/<int:post_id>', methods=['POST'])
def add_comment(post_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '요청 데이터가 없습니다'}), 400
            
        comment_text = data.get('comment', '').strip()
        
        if not comment_text:
            return jsonify({'error': '댓글 내용이 없습니다'}), 400
        
        post = next((post for post in posts if post['id'] == post_id), None)
        if not post:
            return jsonify({'error': '게시물을 찾을 수 없습니다'}), 404
            
        is_invalid, error_message = check_content(comment_text)
        if is_invalid:
            return jsonify({'error': error_message}), 400
            
        comment = {
            'id': len(post['comments']) + 1,
            'text': comment_text,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'user_ip': request.remote_addr
        }
        post['comments'].append(comment)
        
        # 데이터 저장
        save_data()
        
        return jsonify({'success': True, 'comment': comment})
            
    except Exception as e:
        print('Add comment error:', str(e))
        return jsonify({'error': '댓글 작성 중 오류가 발생했습니다'}), 500

@app.route('/comment/<int:post_id>/<int:comment_id>', methods=['DELETE'])
def delete_comment(post_id, comment_id):
    for post in posts:
        if post['id'] == post_id:
            for i, comment in enumerate(post['comments']):
                if comment['id'] == comment_id:
                    # 댓글 작성자만 삭제할 수 있도록 확인
                    if comment['user_ip'] == request.remote_addr:
                        del post['comments'][i]
                        return jsonify({'success': True})
                    else:
                        return jsonify({'error': '권한이 없습니다'}), 403
            return jsonify({'error': '댓글을 찾을 수 없습니다'}), 404
    return jsonify({'error': '게시물을 찾을 수 없습니다'}), 404

@app.route('/comment/<int:post_id>/<int:comment_id>', methods=['PUT'])
def update_comment(post_id, comment_id):
    data = request.json
    new_text = data.get('comment', '').strip()
    
    if not new_text:
        return jsonify({'error': '댓글 내용이 없습니다'}), 400
    
    # 욕설/비난/도배 확인
    is_invalid, error_message = check_content(new_text)
    if is_invalid:
        return jsonify({'error': error_message}), 400
        
    for post in posts:
        if post['id'] == post_id:
            for comment in post['comments']:
                if comment['id'] == comment_id:
                    if comment['user_ip'] == request.remote_addr:
                        comment['text'] = new_text
                        comment['edited'] = True
                        comment['edited_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        return jsonify({'success': True, 'comment': comment})
                    else:
                        return jsonify({'error': '권한이 없습니다'}), 403
            return jsonify({'error': '댓글을 찾을 수 없습니다'}), 404
    return jsonify({'error': '게시물을 찾을 수 없습니다'}), 404

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_message = data.get('message', '').strip()
    
    if not user_message:
        return jsonify({'error': '메시지가 없습니다'}), 400
    
    try:
        # 한국어로 응답하도록 프롬프트 수정
        prompt = f"""다음 질문에 대해 한국어로 친절하게 답변해주세요: {user_message}
        답변은 명확하고 이해하기 쉽게 작성해주세요."""
        
        response = model.generate_content(prompt)
        
        # 응답이 None이거나 text 속성이 없는 경우 처리
        if not response or not hasattr(response, 'text'):
            return jsonify({
                'success': True,
                'response': '죄송합니다. 현재 답변을 생성할 수 없습니다. 잠시 후 다시 시도해주세요.'
            })
        
        return jsonify({
            'success': True,
            'response': response.text
        })
    except Exception as e:
        print('Gemini API Error:', str(e))
        return jsonify({
            'success': True,
            'response': '죄송합니다. 일시적인 오류가 발생했습니다. 다시 질문해 주시겠어요?'
        })

@app.route('/search', methods=['GET'])
def search():
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify({'success': True, 'posts': []})
    
    # 검색 결과 필터링
    search_results = []
    for post in posts:
        content = post.get('content', '').lower()
        if query in content:
            post_copy = post.copy()
            post_copy['liked_by'] = list(post['liked_by'])
            search_results.append(post_copy)
    
    return jsonify({
        'success': True,
        'posts': search_results
    })

# 매일 자정에 글작성 횟수 초기화
def reset_post_counts():
    global post_counts
    post_counts = {}
    save_data()  # 초기화된 데이터 저장

# 스케줄러 설정
scheduler = BackgroundScheduler()
scheduler.add_job(reset_post_counts, 'cron', hour=0, minute=0)
scheduler.start()

if __name__ == '__main__':
    app.run(debug=True) 
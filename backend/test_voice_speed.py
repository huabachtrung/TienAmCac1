import requests, time

def test_audiobook():
    test_path = r'C:\Users\Admin\Desktop\TienAmCac\backend\assets\test_story.txt'
    files = {'file': open(test_path, 'rb')}
    data = {'start_chapter': '1', 'end_chapter': '2'}
    resp = requests.post('http://localhost:8000/api/upload', files=files, data=data)
    print('Upload:', resp.status_code)
    if resp.status_code != 200:
        print('Upload failed:', resp.text)
        return
    job = resp.json()
    job_id = job['job_id']
    print('Job ID:', job_id)

    start = time.time()
    last_voice = 0
    while True:
        time.sleep(2)
        r = requests.get(f'http://localhost:8000/api/jobs/{job_id}')
        data = r.json()
        status = data['status']
        prog = data['progress']
        print(f"Status: {status}, Voice: {prog['voice']}%, FX: {prog['fx']}%, Mix: {prog['mixing']}%")
        if status == 'done':
            print('DONE! Output:', data.get('output_path'))
            break
        if status == 'failed':
            print('FAILED:', data.get('error'))
            break
        if prog['voice'] > last_voice:
            print(f"  -> Voice progress: {prog['voice']}%")
            last_voice = prog['voice']
        if time.time() - start > 180:
            print('Timeout')
            break

if __name__ == '__main__':
    test_audiobook()

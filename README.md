
# CARE: Clinical Assistance &amp; Resource Engine

一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。#

## 聲明

暫無

## 開發步驟

建立虛擬環境

```
python -m venv venv
```

進入虛擬環境

```
venv\Scripts\activate
```

安裝套件

```
pip install -r requirements.txt
```

啟動 Fast Api

```
uvicorn app.main:app --reload --port 8000
```


''' 
跑測試 
python -m pytest tests/ -v -s
'''

'''
Swagger api 文件
http://127.0.0.1:8000/docs
'''
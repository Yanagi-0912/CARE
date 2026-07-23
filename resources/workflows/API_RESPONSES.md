# 📋 API 響應示例 & 狀態碼

## 概述

本文檔展示所有可能的 API 響應格式和對應的 HTTP 狀態碼。

---

## 多媒體處理 API

### Webhook URL
```
POST http://localhost:5678/webhook/multimedia-process
```

### ✅ 成功響應

#### 音頻/視頻轉錄成功
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "success",
  "type": "audio",
  "data": {
    "text": "轉錄的文本內容",
    "confidence": 0.95,
    "language": "zh",
    "duration_seconds": 120
  },
  "processedAt": "2024-07-08T10:30:45Z"
}
```

#### 圖片 OCR 成功
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "success",
  "type": "image",
  "data": {
    "text": "圖片中的文字內容\n包含多行\n文字識別結果",
    "languages": [
      {
        "code": "zh",
        "confidence": 0.98
      }
    ],
    "primary_language": "zh",
    "text_regions": 3
  },
  "processedAt": "2024-07-08T10:30:45Z"
}
```

#### 文檔解析成功
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "success",
  "type": "document",
  "data": {
    "text": "提取的文檔文本內容",
    "pages": 5,
    "format": "pdf",
    "metadata": {
      "title": "文檔標題",
      "author": "作者名稱",
      "created": "2024-07-01"
    }
  },
  "processedAt": "2024-07-08T10:30:45Z"
}
```

### ❌ 錯誤響應

#### 檔案類型不支持 (415)
```json
HTTP/1.1 415 Unsupported Media Type
Content-Type: application/json

{
  "status": "error",
  "code": "UNSUPPORTED_FILE_TYPE",
  "message": "File type exe is not supported",
  "supported": {
    "audio": ["mp3", "wav", "m4a", "flac", "ogg", "opus"],
    "video": ["mp4", "mov", "avi", "mkv", "webm"],
    "image": ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp", "svg"],
    "document": ["txt", "md", "csv", "json", "pdf", "docx", "xlsx", "pptx", "html", "htm"]
  }
}
```

#### 檔案太大 (413)
```json
HTTP/1.1 413 Payload Too Large
Content-Type: application/json

{
  "status": "error",
  "code": "FILE_TOO_LARGE",
  "message": "File size 150MB exceeds maximum 100MB",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 檔案為空 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "FILE_EMPTY",
  "message": "File is empty",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 沒有副檔名 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "NO_EXTENSION",
  "message": "File has no extension",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 沒有上傳檔案 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "NO_FILE",
  "message": "No file provided",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 處理超時 (504)
```json
HTTP/1.1 504 Gateway Timeout
Content-Type: application/json

{
  "status": "error",
  "code": "PROCESSING_TIMEOUT",
  "message": "Request exceeded maximum time of 300 seconds",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 內部服務錯誤 (500)
```json
HTTP/1.1 500 Internal Server Error
Content-Type: application/json

{
  "status": "error",
  "code": "PROCESSING_ERROR",
  "message": "ASR service returned error: Connection refused",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

---

## TTS (文本轉語音) API

### Webhook URL
```
POST http://localhost:5678/webhook/tts
Content-Type: application/json
```

### 📝 請求格式

#### 基本請求
```json
{
  "text": "你好，世界",
  "language": "zh"
}
```

#### 完整請求 (所有可選參數)
```json
{
  "text": "你好，這是一個完整的 TTS 請求示例。",
  "language": "zh",
  "locale": "zh-TW",
  "voice": "default",
  "speed": 1.0,
  "pitch": 1.0
}
```

### ✅ 成功響應

#### TTS 合成成功 (200)
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "success",
  "data": {
    "audio_url": "http://localhost:8300/audio/b5d8c4a2_1720419045.wav",
    "duration_ms": 3500,
    "language": "zh",
    "voice": "default",
    "speed": 1.0,
    "pitch": 1.0,
    "text_length": 25,
    "synthesized_at": "2024-07-08T10:30:45Z"
  }
}
```

#### 使用自定義速度和音調
```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "success",
  "data": {
    "audio_url": "http://localhost:8300/audio/a3f9e1c5_1720419045.wav",
    "duration_ms": 2800,
    "language": "zh",
    "voice": "default",
    "speed": 1.2,
    "pitch": 0.9,
    "text_length": 25,
    "synthesized_at": "2024-07-08T10:30:45Z"
  }
}
```

### ❌ 錯誤響應

#### 驗證失敗 - 文字為空 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "Missing text parameter",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 驗證失敗 - 文字太短 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "Text must be at least 1 character(s)",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 驗證失敗 - 文字太長 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "Text exceeds maximum length of 5000 characters. Current: 5250",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 驗證失敗 - 速度無效 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "Speed must be between 0.5 and 2.0",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 驗證失敗 - 音調無效 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "Pitch must be between 0.5 and 2.0",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### 不支持的語言 (400)
```json
HTTP/1.1 400 Bad Request
Content-Type: application/json

{
  "status": "error",
  "code": "UNSUPPORTED_LANGUAGE",
  "message": "The requested language is not supported.",
  "language": "en",
  "route": "unsupported",
  "supported_languages": ["zh", "taiwanese"],
  "supported_locales": ["zh-TW", "zh-CN", "nan"]
}
```

#### 語言未實現 - 台語 (501)
```json
HTTP/1.1 501 Not Implemented
Content-Type: application/json

{
  "status": "error",
  "code": "TTS_NOT_IMPLEMENTED",
  "message": "Taiwanese TTS is not implemented yet.",
  "language": "taiwanese",
  "estimated_release": "Q4 2024"
}
```

#### TTS 服務超時 (504)
```json
HTTP/1.1 504 Gateway Timeout
Content-Type: application/json

{
  "status": "error",
  "code": "PROCESSING_TIMEOUT",
  "message": "TTS service request exceeded maximum time of 60 seconds",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

#### TTS 服務錯誤 (500)
```json
HTTP/1.1 500 Internal Server Error
Content-Type: application/json

{
  "status": "error",
  "code": "PROCESSING_ERROR",
  "message": "TTS service returned error: Connection refused",
  "timestamp": "2024-07-08T10:30:45Z"
}
```

---

## n8n Admin API

### 列表工作流
```
GET http://localhost:5678/api/v1/workflows
```

#### 成功響應 (200)
```json
{
  "data": [
    {
      "id": "yKC658rkbLy0I3KC",
      "name": "multimedia process - optimized",
      "active": true,
      "nodes": 11,
      "connections": {
        "Webhook": [["Validate & Parse File"]],
        "Validate & Parse File": [["Switch"]],
        ...
      }
    },
    {
      "id": "tts-webhook",
      "name": "tts webhook - optimized",
      "active": false,
      "nodes": 7,
      "connections": {...}
    }
  ],
  "nodesExist": true
}
```

### 取得單個工作流
```
GET http://localhost:5678/api/v1/workflows/{workflow_id}
```

#### 成功響應 (200)
```json
{
  "id": "yKC658rkbLy0I3KC",
  "name": "multimedia process - optimized",
  "active": true,
  "nodes": [...],
  "connections": {...},
  "settings": {
    "executionOrder": "v1",
    "binaryMode": "separate"
  },
  "createdAt": "2024-07-01T10:00:00Z",
  "updatedAt": "2024-07-08T15:30:45Z"
}
```

### 列表工作流執行
```
GET http://localhost:5678/api/v1/workflows/{workflow_id}/executions
```

#### 成功響應 (200)
```json
{
  "data": [
    {
      "id": "exec-123456",
      "workflowId": "yKC658rkbLy0I3KC",
      "mode": "webhook",
      "status": "success",
      "startedAt": "2024-07-08T10:30:00Z",
      "stoppedAt": "2024-07-08T10:30:45Z",
      "executionTime": 45000,
      "data": {...}
    }
  ],
  "nodesExist": true
}
```

---

## 狀態碼總結

| 代碼 | 含義 | 場景 |
|------|------|------|
| **200** | OK | 處理成功 |
| **400** | Bad Request | 驗證失敗、缺少必要參數 |
| **413** | Payload Too Large | 檔案超過大小限制 |
| **415** | Unsupported Media Type | 不支持的檔案類型 |
| **501** | Not Implemented | 功能暫未實現（台語TTS） |
| **504** | Gateway Timeout | 請求超時 |
| **500** | Internal Server Error | 內部處理錯誤 |

---

## cURL 測試示例

### 測試多媒體處理

#### 上傳音頻檔案
```bash
curl -F "file=@audio.mp3" \
  http://localhost:5678/webhook/multimedia-process
```

#### 上傳圖片檔案
```bash
curl -F "file=@image.png" \
  http://localhost:5678/webhook/multimedia-process
```

### 測試 TTS

#### 基本 TTS 請求
```bash
curl -X POST http://localhost:5678/webhook/tts \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好，這是一個測試。",
    "language": "zh"
  }'
```

#### 進階 TTS 請求
```bash
curl -X POST http://localhost:5678/webhook/tts \
  -H "Content-Type: application/json" \
  -d '{
    "text": "速度快一點的測試。",
    "language": "zh",
    "locale": "zh-TW",
    "speed": 1.2,
    "pitch": 1.0
  }' \
  | jq '.'
```

### 獲取工作流列表
```bash
curl -X GET http://localhost:5678/api/v1/workflows \
  -H "Content-Type: application/json" \
  | jq '.'
```

---

## 常用工具

### Postman
- 導入: `CARE_n8n_Collection.postman_collection.json`
- 支持: 自動環境變量、測試腳本

### cURL
- 簡單和快速的測試
- 支持所有 HTTP 方法
- 適合腳本自動化

### Insomnia
- 類似 Postman 的替代品
- 原生支援 GraphQL
- 環境管理

---

**最後更新**: 2024-07-08  
**版本**: 2.0

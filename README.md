# Flow2API

<div align="center">

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.119.0-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-supported-blue.svg)](https://www.docker.com/)

**A full-featured OpenAI-compatible API service providing a unified interface for Google Flow**

**Supports Banana Pro - Unlimited generations with reverse account pool, load balancing, auto AT refresh, caching strategy, and proxy support.**

Join QQ group: 1073237297

</div>

## ✨ Key Features

- 🎨 **Image Generation** - Text-to-image and image-to-image
- 🎬 **Video Generation** - Text-to-video and image-to-video
- 🎞️ **Start-End Frame Video** - First-last frame video generation
- 🍌 **Banana Pro Support** - Unlimited generations with reverse account pool
- 🔄 **AT/ST Auto Refresh** - Automatic token refresh when expired
- 📊 **Balance Display** - Real-time VideoFX Credits query
- 🚀 **Load Balancing** - Multi-token rotation and concurrency control
- 🌐 **Proxy Support** - HTTP/SOCKS5 proxy support
- 📱 **Web Admin Panel** - Intuitive token and configuration management
- 🎨 **Image Generation Multi-turn Conversation**
- 🧩 **Gemini Official Request Compatible** - Supports `generateContent`, `streamGenerateContent`, `systemInstruction`, `contents.parts`
- ✅ **Gemini Official Format Verified** - Tested with real tokens for `/models/{model}:generateContent`

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose (recommended)
- Or Python 3.8+

### Banana Pro Setup (Recommended)

Banana Pro offers **unlimited generations** - perfect for high-volume API usage. The account pool supports:
- **Load Balancing** - Automatic distribution across multiple accounts
- **AT Auto Refresh** - Seamless token renewal without service interruption
- **Caching Strategy** - Reduced redundant API calls
- **Proxy Support** - Built-in proxy rotation

### Method 1: Docker Deployment (Recommended)

#### Standard Mode (No Proxy)

```bash
# Clone the project
git clone https://github.com/prakersh/flow2api.git
cd flow2api

# Start services
docker-compose up -d

# View logs
docker-compose logs -f
```

> Note: Docker Compose mounts `./tmp:/app/tmp` by default. If cache timeout is set to `0`, semantics are "no automatic expiration delete". The `tmp` mount is also needed to preserve cached files after container rebuild.

#### Proxy Mode (WARP)

```bash
# Start with WARP proxy
docker-compose -f docker-compose.warp.yml up -d

# View logs
docker-compose -f docker-compose.warp.yml logs -f
```

#### Docker Headed Captcha Mode (browser / personal)

> Suitable for virtualization desktop needs, enabling headed browser captcha within the container.
> This mode starts `Xvfb + Fluxbox` for container internal visualization and sets `ALLOW_DOCKER_HEADED_CAPTCHA=true`.
> Only application ports are exposed, no remote desktop connection ports.

```bash
# Start headed mode (first run recommended with --build)
docker compose -f docker-compose.headed.yml up -d --build

# View logs
docker compose -f docker-compose.headed.yml logs -f
```

- API port: `8000`
- After entering admin panel, set captcha method to `browser` or `personal`

### Method 2: Local Deployment

```bash
# Clone the project
git clone https://github.com/prakersh/flow2api.git
cd flow2api

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start service
python main.py
```

### First Access

After the service starts, access the admin panel at: **http://localhost:38000**

- **Username**: `admin`
- **Password**: `admin`

After first login, please change the password immediately!

### Model Testing Page

Visit **http://localhost:38000/test** to access the built-in model testing page, supporting:
- Browse all available models by category (image generation, video generation, etc.)
- Enter prompts for one-click testing
- Streaming display of generation progress

### Adding Tokens

1. Log in to [Flow](https://labs.google.com/fx) and get your session token (ST)
2. Open Admin Panel → Tokens → Add Token
3. Enter your Google account email and session token
4. For automatic token refresh, install the [Flow2API Token Updater](https://github.com/TheSmallHanCat/Flow2API-Token-Updater)

### Quick Start: Your First API Call

After adding a token:

1. **Find your token ID**: Admin Panel → Tokens → Copy the ID (number) from the token list
2. **Test the API**:

```bash
# Image generation
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image",
    "messages": [{"role": "user", "content": "A beautiful sunset over the ocean"}]
  }'
```

**Check server status**: Visit `http://localhost:38000/api/status` or Admin Panel → Settings

### Understanding Tokens

- **ST (Session Token)**: Google account session, used to obtain AT tokens
- **AT (Access Token)**: Actual API access token, expires periodically
- Token refresh is automatic when enabled

### API Documentation

For detailed API documentation, visit:
- Swagger UI: `http://localhost:38000/docs`
- ReDoc: `http://localhost:38000/redoc`

## 📖 API Usage Examples

### Image Generation

```bash
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image",
    "messages": [{"role": "user", "content": "A cute cat"}]
  }'
```

### Image Generation with Base64

```bash
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this image:"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YOUR_BASE64_IMAGE"}}
      ]
    }]
  }'
```

### Video Generation (Text-to-Video)

```bash
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3.1_t2v",
    "messages": [{"role": "user", "content": "A bird flying over the ocean"}]
  }'
```

### Video Generation (Image-to-Video)

```bash
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "veo_3.1_i2v",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "https://your-image-url.com/image.png"}},
        {"type": "text", "text": "Animate: Make this image come alive"}
      ]
    }]
  }'
```

### Video Generation (Start-End Frames)

```bash
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: "application/json" \
  -d '{
    "model": "veo_3.1_i2v",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "Start frame."},
        {"type": "image_url", "image_url": {"url": "https://your-start-image-url.com/start.png"}},
        {"type": "text", "text": "End frame."},
        {"type": "image_url", "image_url": {"url": "https://your-end-image-url.com/end.png"}},
        {"type": "text", "text": "Animate: Transition from start to end"}
      ]
    }]
  }'
```

### Streaming Response

```bash
curl -X POST "http://localhost:38000/v1/chat/completions" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3.1-flash-image",
    "messages": [{"role": "user", "content": "Write a story"}],
    "stream": true
  }'
```

### Gemini Native Format

For streaming responses, replace the path with `:streamGenerateContent?alt=sse`.

```bash
curl -X POST "http://localhost:38000/models/gemini-3.1-flash-image:generateContent" \
  -H "x-goog-api-key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"parts": [{"text": "Hello"}]}]
  }'
```

## ⚙️ Configuration

### Main Settings

| Setting | Description | Default |
|---------|-------------|---------|
| API Key | API authentication key | han1234 |
| Admin Username | Admin panel username | admin |
| Admin Password | Admin panel password | admin |

### Advanced Settings

| Setting | Description | Default |
|---------|-------------|---------|
| Timeout | API request timeout (seconds) | 120 |
| Max Retries | Maximum retry attempts | 4 |
| Cache Enabled | Enable response caching | false |
| Cache Timeout | Cache expiration (seconds) | 7200 |

### Captcha Settings

Flow adds additional captcha verification. Choose your preferred method:

1. **YesCaptcha** - Register at [YesCaptcha](https://yescaptcha.com/i/13Xd8K) and get API key
2. **Browser Mode** - Headed browser in container
3. **Personal Mode** - Local resident browser tabs (recommended for high performance)

## 🐛 Troubleshooting

### Token Validation Failed

1. Check if Google account session is still valid
2. Try refreshing the token in Admin Panel → Tokens
3. Ensure ST token has sufficient permissions

### Captcha Verification Failed

1. Check captcha configuration in Admin Panel → Settings
2. For YesCaptcha, verify API key is correct
3. For browser mode, check container logs for browser errors

### Video Generation Failed

1. Check VideoFX Credits balance
2. Verify token has video generation enabled
3. Try reducing generation frequency

## 📝 License

MIT License - See [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Flow2API Original Project](https://github.com/TheSmallHanCat/flow2api) by TheSmallHanCat
- Google Flow / VideoFX for providing generative AI capabilities

## 📧 Contact

For issues and feature requests, please open an issue on GitHub.

Join QQ group: 1073237297

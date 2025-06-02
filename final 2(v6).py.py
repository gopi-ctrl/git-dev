
from flask import Flask, render_template_string, make_response, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
import uuid
import base64
import logging
import os
import time
import socket

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SECRET_KEY'] = os.urandom(24).hex()
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

# Store connected users, chat history, and files
users = {}  # {user_id: {'sid': sid, 'room': room, 'username': username, 'connection_time': timestamp}}
chat_history = {}  # Per-room chat history
files = {}  # {file_id: {name, data, timestamp}}

# Function to detect local IP address
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        logger.error(f"Error detecting local IP: {str(e)}")
        return "127.0.0.1"  # Fallback to localhost

# Clean up old files (older than 1 hour)
def cleanup_files():
    current_time = time.time()
    expired = [fid for fid, info in files.items() if current_time - info['timestamp'] > 3600]
    for fid in expired:
        del files[fid]
    logger.info(f"Cleaned up {len(expired)} expired files")

# HTML/JavaScript client code for Edge 2 Meet
INDEX_HTML = r'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Edge 2 Meet</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #e2e8f0;
            font-family: 'Poppins', sans-serif;
            min-height: 100vh;
            overflow-x: hidden;
        }

        #header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 80px;
            background: rgba(15, 23, 42, 0.95);
            backdrop-filter: blur(8px);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 0 24px;
            z-index: 200;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
        }

        #header h1 {
            font-size: 1.75rem;
            font-weight: 600;
            color: #fff;
            margin-right: 16px;
        }

        .left-logo {
            position: absolute;
            left: 24px;
            top: 20px;
        }

        .right-logo {
            position: absolute;
            right: 24px;
            top: 20px;
        }

        #logo {
            width: 40px;
            height: 40px;
            object-fit: contain;
            transition: transform 0.3s ease;
        }

        #logo:hover {
            transform: rotate(360deg);
        }

        #info-box {
            background: rgba(30, 41, 59, 0.9);
            backdrop-filter: blur(6px);
            border-radius: 12px;
            padding: 8px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            font-size: 0.9rem;
            font-weight: 500;
            color: #fff;
            transition: transform 0.3s ease;
        }

        #info-box:hover {
            transform: scale(1.05);
        }

        #recording-logo {
            display: none;
            color: #f43f5e;
            animation: pulse 1.5s infinite;
        }

        #recording-logo.active {
            display: inline-block;
        }

        #videos {
            display: grid;
            gap: 16px;
            padding: 16px;
            background: rgba(30, 41, 59, 0.8);
            border-radius: 16px;
            margin: 96px 16px 96px;
            width: calc(100% - 32px);
            box-sizing: border-box;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
            transition: width 0.3s ease;
            /* Ensure no scrolling */
            overflow: hidden;
            /* Fix the height to fit the viewport */
            height: calc(100vh - 80px - 80px - 32px); /* Subtract header (80px), toolbar (80px), and padding/margins (32px) */
        }

        #videos.chat-active {
            width: calc(100% - 400px);
        }

        .video-container {
            position: relative;
            width: 100%;
            aspect-ratio: 16 / 9;
            border-radius: 12px;
            overflow: hidden;
            transition: transform 0.3s ease;
        }

        video {
            width: 100%;
            height: 100%;
            background: #000;
            border-radius: 12px;
            border: 2px solid #475569;
            object-fit: cover;
            transition: border-color 0.3s ease, box-shadow 0.3s ease;
        }

        video.speaking {
            border-color: #22c55e;
            box-shadow: 0 0 20px rgba(34, 197, 94, 0.6);
        }

        video:hover {
            transform: scale(1.02);
        }

        .user-name {
            position: absolute;
            top: 8px;
            left: 8px;
            background: rgba(0, 0, 0, 0.7);
            color: #fff;
            padding: 4px 12px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 500;
        }

        .mute-indicator {
            position: absolute;
            bottom: 8px;
            right: 8px;
            background: rgba(0, 0, 0, 0.7);
            padding: 6px;
            border-radius: 50%;
            color: #fff;
            font-size: 1rem;
            display: none;
        }

        .mute-indicator.active {
            display: block;
        }

        .mute-indicator.audio {
            right: 38px;
        }

        #chat-section {
            position: fixed;
            top: 0;
            right: 0;
            height: 100vh;
            width: 360px;
            background: rgba(30, 41, 59, 0.95);
            backdrop-filter: blur(8px);
            border-left: 1px solid #475569;
            padding: 16px;
            transform: translateX(100%);
            transition: transform 0.3s ease;
            z-index: 200;
            box-shadow: -4px 0 12px rgba(0, 0, 0, 0.4);
        }

        #chat-section.active {
            transform: translateX(0);
        }

        #chat-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #475569;
        }

        #chat-close {
            cursor: pointer;
            color: #94a3b8;
            transition: color 0.2s ease;
        }

        #chat-close:hover {
            color: #fff;
        }

        #chat-messages {
            height: calc(100% - 120px);
            overflow-y: auto;
            margin-bottom: 16px;
            display: flex;
            flex-direction: column-reverse;
            padding: 12px;
        }

        .message {
            max-width: 80%;
            margin: 8px 0;
            padding: 12px 16px;
            border-radius: 12px;
            font-size: 0.9rem;
            line-height: 1.4;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s ease;
        }

        .message:hover {
            transform: translateY(-2px);
        }

        .message .text {
            word-break: break-word;
        }

        .message .timestamp {
            font-size: 0.7rem;
            color: #94a3b8;
            margin-top: 4px;
            text-align: right;
        }

        .sent {
            align-self: flex-end;
            background: #3b82f6;
            color: #fff;
        }

        .received {
            align-self: flex-start;
            background: #475569;
            color: #e2e8f0;
        }

        #chat-input {
            background: #2d3748;
            border: 1px solid #475569;
            border-radius: 8px 0 0 8px;
            padding: 10px;
            color: #fff;
            font-size: 0.9rem;
        }

        #send-chat {
            background: #3b82f6;
            border-radius: 0 8px 8px 0;
            padding: 10px 16px;
            transition: background 0.2s ease;
        }

        #send-chat:hover {
            background: #2563eb;
        }

        #chat-alert {
            position: absolute;
            top: 16px;
            left: 16px;
            padding: 8px 16px;
            background: #22c55e;
            color: #fff;
            border-radius: 8px;
            font-size: 0.8rem;
            display: none;
            z-index: 250;
            animation: pulse 2s infinite;
        }

        #bottom-toolbar {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: rgba(15, 23, 42, 0.95);
            backdrop-filter: blur(8px);
            padding: 12px;
            display: flex;
            justify-content: center;
            gap: 12px;
            flex-wrap: wrap;
            z-index: 100;
            box-shadow: 0 -2px 10px rgba(0, 0, 0, 0.3);
        }

        .control-btn, .action-btn {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 16px;
            border-radius: 10px;
            background: #3b82f6;
            color: #fff;
            font-size: 0.9rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
        }

        .control-btn:hover, .action-btn:hover {
            background: #2563eb;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .control-btn.active {
            background: #f43f5e;
        }

        .control-btn.active:hover {
            background: #e11d48;
        }

        .action-btn {
            background: #475569;
        }

        .action-btn:hover {
            background: #334155;
        }

        #leave-meeting-btn {
            background: #f43f5e;
        }

        #leave-meeting-btn:hover {
            background: #e11d48;
        }

        .badge {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(30, 41, 59, 0.9);
            padding: 8px 16px;
            border-radius: 9999px;
            border: 1px solid #475569;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .badge:hover {
            background: #475569;
            transform: scale(1.05);
        }

        #toggle-chat {
            position: relative;
        }

        #unread-badge {
            position: absolute;
            top: -8px;
            right: -8px;
            background: #f43f5e;
            color: #fff;
            border-radius: 50%;
            width: 20px;
            height: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.7rem;
            display: none;
        }

        #room-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.85);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            animation: fadeIn 0.3s ease;
        }

        #room-modal.hidden {
            display: none;
        }

        #main-ui {
            display: none;
            animation: fadeIn 0.3s ease;
        }

        #main-ui.active {
            display: flex;
        }

        #participants-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.85);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }

        #participants-modal.active {
            display: flex;
        }

        #participants-list {
            background: rgba(30, 41, 59, 0.95);
            padding: 24px;
            border-radius: 16px;
            max-width: 400px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
            border: 1px solid #475569;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
        }

        #participants-list h3 {
            font-size: 1.5rem;
            font-weight: 600;
            color: #fff;
            margin-bottom: 16px;
        }

        #participants-list ul {
            list-style: none;
            padding: 0;
        }

        #participants-list li {
            padding: 12px 0;
            border-bottom: 1px solid #475569;
            display: flex;
            justify-content: space-between;
            font-size: 0.9rem;
            color: #e2e8f0;
        }

        #participants-close {
            margin-top: 16px;
            padding: 10px 20px;
            background: #3b82f6;
            border-radius: 10px;
            text-align: center;
            cursor: pointer;
            color: #fff;
            font-weight: 500;
            transition: background 0.2s ease;
        }

        #participants-close:hover {
            background: #2563eb;
        }

        #file-transfer-section {
            position: fixed;
            bottom: 96px;
            right: 16px;
            background: rgba(30, 41, 59, 0.95);
            backdrop-filter: blur(8px);
            padding: 16px;
            border-radius: 12px;
            width: 300px;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
            z-index: 150;
            transition: all 0.3s ease;
        }

        #file-transfer-section.hidden {
            display: none;
        }

        #file-input {
            background: #2d3748;
            border: 1px solid #475569;
            border-radius: 8px;
            padding: 8px;
            color: #fff;
            font-size: 0.85rem;
        }

        #error-message, #notification {
            position: fixed;
            top: 16px;
            right: 16px;
            padding: 12px 24px;
            border-radius: 10px;
            z-index: 300;
            display: none;
            max-width: 300px;
            font-size: 0.9rem;
            animation: slideIn 0.3s ease;
        }

        #error-message {
            background: #f43f5e;
            color: #fff;
        }

        #notification {
            background: #22c55e;
            color: #fff;
        }

        #settings-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.85);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }

        #settings-modal.active {
            display: flex;
        }

        #settings-content {
            background: rgba(30, 41, 59, 0.95);
            padding: 24px;
            border-radius: 16px;
            max-width: 400px;
            width: 90%;
            border: 1px solid #475569;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
        }

        #settings-content h3 {
            font-size: 1.5rem;
            font-weight: 600;
            color: #fff;
            margin-bottom: 16px;
        }

        #settings-content label {
            display: block;
            margin-bottom: 8px;
            font-size: 0.9rem;
            color: #e2e8f0;
        }

        #settings-content select {
            width: 100%;
            padding: 10px;
            background: #2d3748;
            border: 1px solid #475569;
            border-radius: 8px;
            color: #e2e8f0;
            font-size: 0.9rem;
            margin-bottom: 16px;
        }

        #settings-close {
            margin-top: 16px;
            padding: 10px 20px;
            background: #3b82f6;
            border-radius: 10px;
            text-align: center;
            cursor: pointer;
            color: #fff;
            font-weight: 500;
            transition: background 0.2s ease;
        }

        #settings-close:hover {
            background: #2563eb;
        }

        @media (max-width: 768px) {
            #videos {
                margin: 88px 8px 80px;
                padding: 8px;
                gap: 8px;
                width: calc(100% - 16px);
            }

            #videos.chat-active {
                width: 100%;
            }

            #chat-section {
                width: 100%;
                max-width: 100%;
            }

            #header {
                padding: 0 16px;
                height: 72px;
            }

            .left-logo {
                top: 16px;
            }

            .right-logo {
                top: 16px;
            }

            #info-box {
                font-size: 0.8rem;
                padding: 6px 12px;
            }

            .control-btn, .action-btn {
                padding: 8px 12px;
                font-size: 0.8rem;
            }

            #file-transfer-section {
                width: 280px;
                right: 8px;
            }
        }

        @media (max-width: 480px) {
            #header h1 {
                font-size: 1.4rem;
            }

            #logo {
                width: 32px;
                height: 32px;
            }

            #info-box {
                font-size: 0.75rem;
                padding: 4px 8px;
            }

            .badge {
                padding: 6px 12px;
                font-size: 0.8rem;
            }

            #participants-list, #settings-content {
                padding: 16px;
                max-width: 90%;
            }
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        @keyframes pulse {
            0% { transform: scale(1); }
            50% { transform: scale(1.1); }
            100% { transform: scale(1); }
        }

        @keyframes pop {
            0% { transform: scale(0.8); opacity: 0; }
            50% { transform: scale(1.05); opacity: 1; }
            100% { transform: scale(1); opacity: 1; }
        }
    </style>
</head>
<body>
    <div id="error-message"></div>
    <div id="notification"></div>
    <div id="header">
        <img src="/static/edge2systems_logo.jpg" alt="Edge 2 Systems Logo" id="logo" class="left-logo">
        <h1>Edge 2 Meet</h1>
        <div id="info-box">
            <i id="recording-logo" class="fas fa-record-vinyl"></i>
            <span id="room-id-display"></span>
            <span id="vc-timer">00:00:00</span>
        </div>
        <img src="/static/indiannavy_logo.jpg" alt="Indian Navy Logo" id="logo" class="right-logo">
    </div>
    <div id="room-modal">
        <div class="bg-gray-800 p-8 rounded-xl shadow-2xl w-full max-w-md">
            <h2 class="text-2xl font-bold mb-6 text-center text-white">Join a Room</h2>
            <div class="flex flex-col gap-4">
                <input id="room-id" type="text" class="p-3 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Enter Room ID">
                <input id="username-input-room" type="text" class="p-3 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Enter your name">
                <button id="join-room" class="p-3 bg-blue-500 hover:bg-blue-600 rounded-lg flex items-center justify-center gap-2 text-white font-medium">
                    <i class="fas fa-sign-in-alt"></i> Join Room
                </button>
            </div>
        </div>
    </div>
    <div id="participants-modal">
        <div id="participants-list">
            <h3>Participants: <span id="participant-count-modal">0</span></h3>
            <ul id="participants-items"></ul>
            <div id="participants-close">Close</div>
        </div>
    </div>
    <div id="settings-modal">
        <div id="settings-content">
            <h3>Settings</h3>
            <label for="microphone-select">Microphone:</label>
            <select id="microphone-select">
                <option value="">Select Microphone</option>
            </select>
            <label for="camera-select">Camera:</label>
            <select id="camera-select">
                <option value="">Select Camera</option>
            </select>
            <label for="quality-select">Video Quality:</label>
            <select id="quality-select">
                <option value="high">High (1080p, 60fps)</option>
                <option value="standard" selected>Standard (720p, 30fps)</option>
                <option value="medium">Medium (480p, 30fps)</option>
                <option value="low">Low (360p, 15fps)</option>
            </select>
            <div id="settings-close">Close</div>
        </div>
    </div>
    <div id="main-ui" class="flex flex-col items-center p-4">
        <div class="w-full max-w-full flex flex-col items-center space-y-6">
            <div id="videos"></div>
        </div>
    </div>
    <div id="chat-section">
        <div id="chat-header">
            <h3 class="text-lg font-semibold">Chat</h3>
            <i id="chat-close" class="fas fa-times fa-lg"></i>
        </div>
        <div id="chat-alert"></div>
        <div id="chat-messages"></div>
        <div class="flex">
            <input id="chat-input" type="text" class="flex-1" placeholder="Type a message...">
            <button id="send-chat" class="flex items-center gap-2">
                <i class="fas fa-paper-plane"></i>
            </button>
        </div>
    </div>
    <div id="bottom-toolbar">
        <div class="badge" id="participant-count">
            <i class="fas fa-users"></i> Participants: 0
        </div>
        <button id="mute-audio" class="control-btn">
            <i class="fas fa-microphone"></i> Mute Audio
        </button>
        <button id="mute-video" class="control-btn">
            <i class="fas fa-video"></i> Mute Video
        </button>
        <button id="share-screen" class="control-btn">
            <i class="fas fa-desktop"></i> Share Screen
        </button>
        <button id="record" class="control-btn">
            <i class="fas fa-record-vinyl"></i> Start Recording
        </button>
        <button id="toggle-chat" class="control-btn">
            <i class="fas fa-comments"></i> Chat
            <span id="unread-badge">0</span>
        </button>
        <button id="toggle-file-transfer" class="control-btn">
            <i class="fas fa-file-upload"></i> File Transfer
        </button>
        <button id="settings-btn" class="control-btn">
            <i class="fas fa-cog"></i> Settings
        </button>
        <button id="leave-meeting-btn" class="control-btn">
            <i class="fas fa-phone-slash"></i> Leave Meeting
        </button>
    </div>
    <div id="file-transfer-section" class="hidden">
        <div class="flex flex-col gap-3">
            <input type="file" id="file-input">
            <button id="send-file" class="control-btn">
                <i class="fas fa-paper-plane"></i> Send File
            </button>
            <button id="cancel-file" class="action-btn">
                <i class="fas fa-times"></i> Cancel
            </button>
        </div>
    </div>

    <script>
        const serverIp = window.location.hostname;
        const socket = io(`http://${serverIp}:5000`, { 
            transports: ['websocket'], 
            reconnection: true, 
            reconnectionAttempts: 5, 
            reconnectionDelay: 1000 
        });
        const localVideo = document.createElement('video');
        let localStream;
        let screenStream;
        let mediaRecorder;
        let recordedChunks = [];
        const peers = {};
        const userId = Math.random().toString(36).substring(2);
        let username = `User ${userId.substring(0, 6)}`;
        let participantCount = 0;
        let isAudioMuted = false;
        let isVideoMuted = false;
        let isScreenSharing = false;
        let isRecording = false;
        let isChatVisible = false;
        let isFileTransferVisible = false;
        let roomId = null;
        let unreadMessages = 0;
        const pendingIceCandidates = {};
        const users = {};
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const analyserNodes = {};
        let currentMicrophone = '';
        let currentCamera = '';
        let currentVideoQuality = 'standard';
        let vcStartTime = null;
        let vcTimerInterval = null;

        function startVCTimer() {
            vcStartTime = Date.now();
            const vcTimerDisplay = document.getElementById('vc-timer');
            vcTimerInterval = setInterval(() => {
                const elapsed = Date.now() - vcStartTime;
                const hours = Math.floor(elapsed / 3600000);
                const minutes = Math.floor((elapsed % 3600000) / 60000);
                const seconds = Math.floor((elapsed % 60000) / 1000);
                vcTimerDisplay.textContent = `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            }, 1000);
        }

        function stopVCTimer() {
            if (vcTimerInterval) {
                clearInterval(vcTimerInterval);
                vcTimerInterval = null;
            }
            document.getElementById('vc-timer').textContent = '00:00:00';
            vcStartTime = null;
        }

        function showError(message) {
            const errorDiv = document.getElementById('error-message');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            setTimeout(() => errorDiv.style.display = 'none', 5000);
        }

        function showNotification(message) {
            const notificationDiv = document.getElementById('notification');
            notificationDiv.textContent = message;
            notificationDiv.style.display = 'block';
            setTimeout(() => notificationDiv.style.display = 'none', 3000);
        }

        function updateVideoSizes() {
            try {
                const containers = document.querySelectorAll('#videos .video-container');
                const count = containers.length;
                const videosContainer = document.getElementById('videos');

                if (!videosContainer) return;

                videosContainer.classList.toggle('chat-active', isChatVisible);

                if (count === 0) {
                    videosContainer.style.display = 'none';
                    return;
                }

                const headerHeight = 80;
                const infoBoxHeight = 48;
                const toolbarHeight = 80;
                const gap = 16;
                const padding = 16;
                const availableHeight = window.innerHeight - headerHeight - infoBoxHeight - toolbarHeight - 2 * padding;
                const availableWidth = isChatVisible 
                    ? window.innerWidth - 360 - 2 * padding 
                    : window.innerWidth - 2 * padding;

                let cols, rows;
                if (count <= 3) {
                    cols = count;
                    rows = 1;
                } else {
                    cols = 3;
                    rows = Math.ceil((count - 3) / 3) + 1;
                }

                const totalGapsX = gap * (cols - 1);
                let videoWidth = (availableWidth - totalGapsX) / cols;
                let videoHeight = videoWidth * (9 / 16);
                let totalHeight = videoHeight * rows + gap * (rows - 1);

                if (totalHeight > availableHeight) {
                    videoHeight = (availableHeight - gap * (rows - 1)) / rows;
                    videoWidth = videoHeight * (16 / 9);
                }

                cols = Math.max(cols, 1);
                rows = Math.max(rows, 1);

                videosContainer.style.display = 'grid';
                videosContainer.style.gridTemplateColumns = `repeat(${cols}, ${videoWidth}px)`;
                videosContainer.style.gridTemplateRows = `repeat(${rows}, ${videoHeight}px)`;
                videosContainer.style.gap = `${gap}px`;
                videosContainer.style.padding = `${padding}px`;
                videosContainer.style.height = `${totalHeight + 2 * padding}px`;
                videosContainer.style.width = isChatVisible ? 'calc(100% - 360px)' : 'calc(100% - 32px)';
                videosContainer.style.boxSizing = 'border-box';
                videosContainer.style.overflow = 'hidden';

                const sortedContainers = Array.from(containers).sort((a, b) => {
                    const idA = a.id.replace('video-container-', '');
                    const idB = b.id.replace('video-container-', '');
                    return idA.localeCompare(idB);
                });

                videosContainer.innerHTML = '';
                sortedContainers.forEach(container => {
                    container.style.width = '100%';
                    container.style.height = '100%';
                    container.style.aspectRatio = '16 / 9';
                    const video = container.querySelector('video');
                    if (video) {
                        video.style.width = '100%';
                        video.style.height = '100%';
                        video.style.objectFit = 'cover';
                    }
                    videosContainer.appendChild(container);
                });
            } catch (err) {
                showError('Failed to update video layout');
            }
        }

        function showParticipantsList() {
            const modal = document.getElementById('participants-modal');
            const list = document.getElementById('participants-items');
            const countSpan = document.getElementById('participant-count-modal');
            list.innerHTML = '';
            countSpan.textContent = participantCount;
            Object.entries(users).forEach(([id, user]) => {
                const li = document.createElement('li');
                const connectionTime = user.connection_time ? 
                    new Date(user.connection_time).toLocaleString('en-US', { 
                        hour: '2-digit', 
                        minute: '2-digit', 
                        second: '2-digit', 
                        hour12: true 
                    }) : 'Unknown';
                li.innerHTML = `<span>${user.username || 'Unknown'}</span><span>${connectionTime}</span>`;
                list.appendChild(li);
            });
            modal.classList.add('active');
        }

        function updateMuteIndicators(user_id, audioMuted, videoMuted) {
            const container = document.getElementById(`video-container-${user_id}`);
            if (container) {
                let audioIndicator = container.querySelector('.mute-indicator.audio');
                let videoIndicator = container.querySelector('.mute-indicator.video');
                
                if (!audioIndicator) {
                    audioIndicator = document.createElement('i');
                    audioIndicator.className = 'fas fa-microphone-slash mute-indicator audio';
                    container.appendChild(audioIndicator);
                }
                if (!videoIndicator) {
                    videoIndicator = document.createElement('i');
                    videoIndicator.className = 'fas fa-video-slash mute-indicator video';
                    container.appendChild(videoIndicator);
                }

                audioIndicator.classList.toggle('active', audioMuted);
                videoIndicator.classList.toggle('active', videoMuted);
            }
        }

        function monitorAudioLevels(user_id, stream) {
            try {
                const analyser = audioContext.createAnalyser();
                analyser.fftSize = 256;
                const source = audioContext.createMediaStreamSource(stream);
                source.connect(analyser);
                analyserNodes[user_id] = analyser;

                const dataArray = new Uint8Array(analyser.frequencyBinCount);
                const video = document.getElementById(`video-${user_id}`);

                function checkAudioLevel() {
                    if (!analyserNodes[user_id] || !video) return;
                    analyser.getByteFrequencyData(dataArray);
                    const average = dataArray.reduce((sum, val) => sum + val, 0) / dataArray.length;
                    const threshold = 8;
                    if (average > threshold && !users[user_id]?.audioMuted) {
                        video.classList.add('speaking');
                    } else {
                        video.classList.remove('speaking');
                    }
                    requestAnimationFrame(checkAudioLevel);
                }
                checkAudioLevel();
            } catch (err) {
                showError(`Failed to monitor audio for user ${user_id}`);
            }
        }

        async function populateDeviceDropdowns() {
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                const microphoneSelect = document.getElementById('microphone-select');
                const cameraSelect = document.getElementById('camera-select');
                
                microphoneSelect.innerHTML = '<option value="">Select Microphone</option>';
                cameraSelect.innerHTML = '<option value="">Select Camera</option>';

                const microphones = devices.filter(device => device.kind === 'audioinput');
                const cameras = devices.filter(device => device.kind === 'videoinput');

                microphones.forEach(device => {
                    const option = document.createElement('option');
                    option.value = device.deviceId;
                    option.text = device.label || `Microphone ${microphoneSelect.options.length}`;
                    if (device.deviceId === currentMicrophone) option.selected = true;
                    microphoneSelect.appendChild(option);
                });

                cameras.forEach(device => {
                    const option = document.createElement('option');
                    option.value = device.deviceId;
                    option.text = device.label || `Camera ${cameraSelect.options.length}`;
                    if (device.deviceId === currentCamera) option.selected = true;
                    cameraSelect.appendChild(option);
                });

                if (microphones.length === 0) {
                    microphoneSelect.innerHTML = '<option value="">No Microphones Available</option>';
                }
                if (cameras.length === 0) {
                    cameraSelect.innerHTML = '<option value="">No Cameras Available</option>';
                }
            } catch (err) {
                showError('Failed to list media devices');
            }
        }

        async function switchMicrophone(deviceId) {
            if (!deviceId) return;
            try {
                const newStream = await navigator.mediaDevices.getUserMedia({
                    audio: { deviceId: { exact: deviceId } },
                    video: currentCamera ? { deviceId: { exact: currentCamera } } : false
                });
                const newAudioTrack = newStream.getAudioTracks()[0];

                if (localStream) {
                    const oldAudioTrack = localStream.getAudioTracks()[0];
                    if (oldAudioTrack) {
                        oldAudioTrack.stop();
                        localStream.removeTrack(oldAudioTrack);
                    }
                    localStream.addTrack(newAudioTrack);
                } else {
                    localStream = newStream;
                }

                localVideo.srcObject = localStream;
                currentMicrophone = deviceId;

                Object.values(peers).forEach(peer => {
                    const sender = peer.getSenders().find(s => s.track?.kind === 'audio');
                    if (sender) sender.replaceTrack(newAudioTrack);
                });

                monitorAudioLevels(userId, localStream);
                showNotification('Microphone switched successfully');
            } catch (err) {
                showError('Failed to switch microphone');
            }
        }

        async function switchCamera(deviceId) {
            if (!deviceId) return;
            try {
                const qualityConstraints = getQualityConstraints(currentVideoQuality);
                const newStream = await navigator.mediaDevices.getUserMedia({
                    video: { 
                        deviceId: { exact: deviceId },
                        width: qualityConstraints.width,
                        height: qualityConstraints.height,
                        frameRate: qualityConstraints.frameRate,
                        aspectRatio: 16 / 9
                    },
                    audio: currentMicrophone ? { deviceId: { exact: currentMicrophone } } : false
                });
                const newVideoTrack = newStream.getVideoTracks()[0];

                if (localStream) {
                    const oldVideoTrack = localStream.getVideoTracks()[0];
                    if (oldVideoTrack) {
                        oldVideoTrack.stop();
                        localStream.removeTrack(oldVideoTrack);
                    }
                    localStream.addTrack(newVideoTrack);
                } else {
                    localStream = newStream;
                }

                localVideo.srcObject = localStream;
                currentCamera = deviceId;

                Object.values(peers).forEach(peer => {
                    const sender = peer.getSenders().find(s => s.track?.kind === 'video');
                    if (sender) sender.replaceTrack(newVideoTrack);
                });

                monitorAudioLevels(userId, localStream);
                showNotification('Camera switched successfully');
            } catch (err) {
                showError('Failed to switch camera');
            }
        }

        function getQualityConstraints(quality) {
            switch (quality) {
                case 'high':
                    return { width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 60 } };
                case 'standard':
                    return { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } };
                case 'medium':
                    return { width: { ideal: 854 }, height: { ideal: 480 }, frameRate: { ideal: 30 } };
                case 'low':
                    return { width: { ideal: 640 }, height: { ideal: 360 }, frameRate: { ideal: 15 } };
                default:
                    return { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } };
            }
        }

        async function switchVideoQuality(quality) {
            if (!currentCamera || quality === currentVideoQuality) return;
            try {
                const qualityConstraints = getQualityConstraints(quality);
                const newStream = await navigator.mediaDevices.getUserMedia({
                    video: { 
                        deviceId: { exact: currentCamera },
                        width: qualityConstraints.width,
                        height: qualityConstraints.height,
                        frameRate: qualityConstraints.frameRate,
                        aspectRatio: 16 / 9
                    },
                    audio: currentMicrophone ? { deviceId: { exact: currentMicrophone } } : false
                });
                const newVideoTrack = newStream.getVideoTracks()[0];

                if (localStream) {
                    const oldVideoTrack = localStream.getVideoTracks()[0];
                    if (oldVideoTrack) {
                        oldVideoTrack.stop();
                        localStream.removeTrack(oldVideoTrack);
                    }
                    localStream.addTrack(newVideoTrack);
                } else {
                    localStream = newStream;
                }

                localVideo.srcObject = localStream;
                currentVideoQuality = quality;

                Object.values(peers).forEach(peer => {
                    const sender = peer.getSenders().find(s => s.track?.kind === 'video');
                    if (sender) sender.replaceTrack(newVideoTrack);
                });

                monitorAudioLevels(userId, localStream);
                showNotification(`Video quality set to ${quality}`);
            } catch (err) {
                showError('Failed to switch video quality');
            }
        }

        async function startVideo() {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                showError('Your browser does not support WebRTC.');
                return false;
            }
            try {
                if (localStream) {
                    localStream.getTracks().forEach(track => track.stop());
                }

                const qualityConstraints = getQualityConstraints(currentVideoQuality);
                const constraints = {
                    video: currentCamera ? { 
                        deviceId: { exact: currentCamera },
                        width: qualityConstraints.width,
                        height: qualityConstraints.height,
                        frameRate: qualityConstraints.frameRate,
                        aspectRatio: 16 / 9
                    } : {
                        width: qualityConstraints.width,
                        height: qualityConstraints.height,
                        frameRate: qualityConstraints.frameRate,
                        aspectRatio: 16 / 9
                    },
                    audio: currentMicrophone ? { 
                        deviceId: { exact: currentMicrophone },
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                        sampleRate: 48000
                    } : {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                        sampleRate: 48000
                    }
                };

                localStream = await navigator.mediaDevices.getUserMedia(constraints);
                localVideo.srcObject = localStream;
                localVideo.muted = true;
                localVideo.autoplay = true;
                localVideo.playsInline = true;
                localVideo.id = `video-${userId}`;
                
                let container = document.getElementById(`video-container-${userId}`);
                if (!container) {
                    container = document.createElement('div');
                    container.id = `video-container-${userId}`;
                    container.className = 'video-container';
                    const nameLabel = document.createElement('div');
                    nameLabel.className = 'user-name';
                    nameLabel.textContent = username;
                    container.appendChild(nameLabel);
                    container.appendChild(localVideo);
                    document.getElementById('videos').appendChild(container);
                } else {
                    container.innerHTML = '';
                    container.className = 'video-container';
                    const nameLabel = document.createElement('div');
                    nameLabel.className = 'user-name';
                    nameLabel.textContent = username;
                    container.appendChild(nameLabel);
                    container.appendChild(localVideo);
                }

                const videoTrack = localStream.getVideoTracks()[0];
                if (videoTrack && !currentCamera) {
                    currentCamera = videoTrack.getSettings().deviceId;
                }
                const audioTrack = localStream.getAudioTracks()[0];
                if (audioTrack && !currentMicrophone) {
                    currentMicrophone = audioTrack.getSettings().deviceId;
                }

                await populateDeviceDropdowns();

                monitorAudioLevels(userId, localStream);
                updateMuteIndicators(userId, isAudioMuted, isVideoMuted);
                updateVideoSizes();

                return true;
            } catch (err) {
                showError('Media access failed. Joining without media.');
                const container = document.createElement('div');
                container.id = `video-container-${userId}`;
                container.className = 'video-container';
                const nameLabel = document.createElement('div');
                nameLabel.className = 'user-name';
                nameLabel.textContent = username;
                const placeholder = document.createElement('div');
                placeholder.style.width = '100%';
                placeholder.style.height = '100%';
                placeholder.style.background = '#000';
                placeholder.style.borderRadius = '12px';
                placeholder.style.display = 'flex';
                placeholder.style.alignItems = 'center';
                placeholder.style.justifyContent = 'center';
                placeholder.style.color = '#fff';
                placeholder.textContent = 'No Video Available';
                container.appendChild(nameLabel);
                container.appendChild(placeholder);
                document.getElementById('videos').appendChild(container);
                updateVideoSizes();
                return false;
            }
        }

        async function startScreenShare() {
            try {
                screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { aspectRatio: 16 / 9 } });
                isScreenSharing = true;
                document.getElementById('share-screen').innerHTML = '<i class="fas fa-desktop"></i> Stop Sharing';
                document.getElementById('share-screen').classList.add('active');
                const videoTrack = screenStream.getVideoTracks()[0];
                localVideo.srcObject = screenStream;
                Object.values(peers).forEach(peer => {
                    const sender = peer.getSenders().find(s => s.track?.kind === 'video');
                    if (sender) sender.replaceTrack(videoTrack);
                });
                videoTrack.onended = stopScreenShare;
            } catch (err) {
                showError('Failed to share screen.');
            }
        }

        function stopScreenShare() {
            if (screenStream) {
                screenStream.getTracks().forEach(track => track.stop());
                screenStream = null;
            }
            isScreenSharing = false;
            document.getElementById('share-screen').innerHTML = '<i class="fas fa-desktop"></i> Share Screen';
            document.getElementById('share-screen').classList.remove('active');
            localVideo.srcObject = localStream;
            if (localStream) {
                const videoTrack = localStream.getVideoTracks()[0];
                Object.values(peers).forEach(peer => {
                    const sender = peer.getSenders().find(s => s.track?.kind === 'video');
                    if (sender && videoTrack) {
                        sender.replaceTrack(videoTrack);
                    }
                });
            }
        }

        async function toggleRecording() {
            if (!isRecording) {
                try {
                    const screenStream = await navigator.mediaDevices.getDisplayMedia({
                        video: { displaySurface: 'browser' },
                        audio: false
                    });

                    const mixedStream = new MediaStream();
                    screenStream.getVideoTracks().forEach(track => mixedStream.addTrack(track));
                    if (localStream && localStream.getAudioTracks().length) {
                        localStream.getAudioTracks().forEach(track => mixedStream.addTrack(track));
                    }

                    mediaRecorder = new MediaRecorder(mixedStream, { mimeType: 'video/webm' });
                    recordedChunks = [];
                    mediaRecorder.ondataavailable = (e) => {
                        if (e.data.size > 0) recordedChunks.push(e.data);
                    };
                    mediaRecorder.onstop = () => {
                        const blob = new Blob(recordedChunks, { type: 'video/webm' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `recording-${new Date().toISOString()}.webm`;
                        a.click();
                        URL.revokeObjectURL(url);
                        screenStream.getTracks().forEach(track => track.stop());
                    };

                    mediaRecorder.start();
                    isRecording = true;
                    document.getElementById('record').innerHTML = '<i class="fas fa-record-vinyl"></i> Stop Recording';
                    document.getElementById('record').classList.add('active');
                    document.getElementById('recording-logo').classList.add('active');

                    screenStream.getVideoTracks()[0].onended = () => {
                        if (isRecording) {
                            toggleRecording();
                        }
                    };
                } catch (err) {
                    showError('Failed to start recording.');
                }
            } else {
                mediaRecorder.stop();
                isRecording = false;
                document.getElementById('record').innerHTML = '<i class="fas fa-record-vinyl"></i> Start Recording';
                document.getElementById('record').classList.remove('active');
                document.getElementById('recording-logo').classList.remove('active');
            }
        }

        function toggleChat() {
            isChatVisible = !isChatVisible;
            document.getElementById('chat-section').classList.toggle('active', isChatVisible);
            document.getElementById('videos').classList.toggle('chat-active', isChatVisible);
            document.getElementById('toggle-chat').innerHTML = `<i class="fas fa-comments"></i> ${isChatVisible ? 'Hide Chat' : 'Chat'}<span id="unread-badge">${unreadMessages}</span>`;
            document.getElementById('toggle-chat').classList.toggle('active', isChatVisible);
            if (isChatVisible) {
                unreadMessages = 0;
                document.getElementById('unread-badge').textContent = unreadMessages;
                document.getElementById('unread-badge').style.display = 'none';
            }
            updateVideoSizes();
        }

        function leaveMeeting() {
            if (localStream) {
                localStream.getTracks().forEach(track => track.stop());
                localStream = null;
            }
            if (screenStream) {
                screenStream.getTracks().forEach(track => track.stop());
                screenStream = null;
            }
            Object.values(peers).forEach(peer => peer.close());
            Object.keys(peers).forEach(key => delete peers[key]);
            Object.keys(pendingIceCandidates).forEach(key => delete pendingIceCandidates[key]);
            Object.keys(analyserNodes).forEach(key => delete analyserNodes[key]);
            document.getElementById('videos').innerHTML = '';
            document.getElementById('main-ui').classList.remove('active');
            document.getElementById('room-modal').classList.remove('hidden');
            document.getElementById('room-id').value = '';
            document.getElementById('username-input-room').value = '';
            document.getElementById('room-id').disabled = false;
            document.getElementById('join-room').disabled = false;
            document.getElementById('join-room').innerHTML = '<i class="fas fa-sign-in-alt"></i> Join Room';
            if (roomId) {
                socket.emit('leave_room', { room: roomId, user_id: userId });
                roomId = null;
            }
            stopVCTimer();
            isAudioMuted = false;
            isVideoMuted = false;
            isScreenSharing = false;
            isRecording = false;
            isChatVisible = false;
            isFileTransferVisible = false;
            unreadMessages = 0;
            currentMicrophone = '';
            currentCamera = '';
            currentVideoQuality = 'standard';
            document.getElementById('mute-audio').innerHTML = '<i class="fas fa-microphone"></i> Mute Audio';
            document.getElementById('mute-audio').classList.remove('active');
            document.getElementById('mute-video').innerHTML = '<i class="fas fa-video"></i> Mute Video';
            document.getElementById('mute-video').classList.remove('active');
            document.getElementById('share-screen').innerHTML = '<i class="fas fa-desktop"></i> Share Screen';
            document.getElementById('share-screen').classList.remove('active');
            document.getElementById('record').innerHTML = '<i class="fas fa-record-vinyl"></i> Start Recording';
            document.getElementById('record').classList.remove('active');
            document.getElementById('toggle-chat').innerHTML = '<i class="fas fa-comments"></i> Chat<span id="unread-badge">0</span>';
            document.getElementById('toggle-chat').classList.remove('active');
            document.getElementById('toggle-file-transfer').innerHTML = '<i class="fas fa-file-upload"></i> File Transfer';
            document.getElementById('toggle-file-transfer').classList.remove('active');
            document.getElementById('chat-section').classList.remove('active');
            document.getElementById('videos').classList.remove('chat-active');
            document.getElementById('file-transfer-section').classList.add('hidden');
            document.getElementById('room-id-display').textContent = '';
            document.getElementById('recording-logo').classList.remove('active');
            showNotification('Left the meeting');
        }

        document.getElementById('toggle-chat').addEventListener('click', toggleChat);

        document.getElementById('chat-close').addEventListener('click', toggleChat);

        document.getElementById('toggle-file-transfer').addEventListener('click', () => {
            isFileTransferVisible = !isFileTransferVisible;
            document.getElementById('file-transfer-section').classList.toggle('hidden', !isFileTransferVisible);
            document.getElementById('toggle-file-transfer').innerHTML = `<i class="fas fa-file-upload"></i> ${isFileTransferVisible ? 'Hide File Transfer' : 'File Transfer'}`;
            document.getElementById('toggle-file-transfer').classList.toggle('active', isFileTransferVisible);
        });

        document.getElementById('leave-meeting-btn').addEventListener('click', leaveMeeting);

        document.getElementById('participant-count').addEventListener('click', showParticipantsList);

        document.getElementById('participants-close').addEventListener('click', () => {
            document.getElementById('participants-modal').classList.remove('active');
        });

        document.getElementById('settings-btn').addEventListener('click', async () => {
            await populateDeviceDropdowns();
            document.getElementById('settings-modal').classList.add('active');
        });

        document.getElementById('settings-close').addEventListener('click', () => {
            document.getElementById('settings-modal').classList.remove('active');
        });

        document.getElementById('microphone-select').addEventListener('change', (e) => {
            const deviceId = e.target.value;
            if (deviceId) switchMicrophone(deviceId);
        });

        document.getElementById('camera-select').addEventListener('change', (e) => {
            const deviceId = e.target.value;
            if (deviceId) switchCamera(deviceId);
        });

        document.getElementById('quality-select').addEventListener('change', (e) => {
            const quality = e.target.value;
            if (quality) switchVideoQuality(quality);
        });

        navigator.mediaDevices.ondevicechange = async () => {
            await populateDeviceDropdowns();
        };

        document.getElementById('send-file').addEventListener('click', () => {
            const file = document.getElementById('file-input').files[0];
            if (!file) {
                showError('Please select a file.');
                return;
            }
            if (file.size > 5 * 1024 * 1024) {
                showError('File size exceeds 5MB limit.');
                return;
            }
            if (!roomId) {
                showError('Not connected to a room.');
                return;
            }
            const reader = new FileReader();
            reader.onload = () => {
                socket.emit('file_upload', {
                    user_id: userId,
                    file_name: file.name,
                    file_data: reader.result.split(',')[1],
                    room: roomId
                });
                showNotification(`You sent file: ${file.name}`);
                document.getElementById('file-input').value = '';
                isFileTransferVisible = false;
                document.getElementById('file-transfer-section').classList.add('hidden');
                document.getElementById('toggle-file-transfer').innerHTML = '<i class="fas fa-file-upload"></i> File Transfer';
                document.getElementById('toggle-file-transfer').classList.remove('active');
            };
            reader.onerror = () => showError('Error reading file.');
            reader.readAsDataURL(file);
        });

        document.getElementById('cancel-file').addEventListener('click', () => {
            document.getElementById('file-input').value = '';
            isFileTransferVisible = false;
            document.getElementById('file-transfer-section').classList.add('hidden');
            document.getElementById('toggle-file-transfer').innerHTML = '<i class="fas fa-file-upload"></i> File Transfer';
            document.getElementById('toggle-file-transfer').classList.remove('active');
        });

        function createPeer(remoteUserId) {
            const peer = new RTCPeerConnection({
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:stun1.l.google.com:19302' },
                    {
                        urls: [
                            'turn:openrelay.metered.ca:80',
                            'turn:openrelay.metered.ca:443',
                            'turn:openrelay.metered.ca:443?transport=tcp'
                        ],
                        username: 'openrelayproject',
                        credential: 'openrelayproject'
                    }
                ]
            });

            pendingIceCandidates[remoteUserId] = [];

            peer.ontrack = (event) => {
                const [remoteStream] = event.streams;
                if (!remoteStream) return;

                let container = document.getElementById(`video-container-${remoteUserId}`);
                if (!container) {
                    container = document.createElement('div');
                    container.id = `video-container-${remoteUserId}`;
                    container.className = 'video-container';
                    const video = document.createElement('video');
                    video.id = `video-${remoteUserId}`;
                    video.autoplay = true;
                    video.playsInline = true;
                    const nameLabel = document.createElement('div');
                    nameLabel.className = 'user-name';
                    nameLabel.textContent = users[remoteUserId]?.username || `User ${remoteUserId.substring(0, 6)}`;
                    container.appendChild(nameLabel);
                    container.appendChild(video);
                    document.getElementById('videos').appendChild(container);
                }

                const video = container.querySelector('video');
                if (video.srcObject !== remoteStream) {
                    video.srcObject = remoteStream;
                    monitorAudioLevels(remoteUserId, remoteStream);
                }

                updateVideoSizes();
            };

            if (localStream) {
                localStream.getTracks().forEach(track => {
                    if (track.readyState === 'live') {
                        peer.addTrack(track, localStream);
                    }
                });
            }

            peer.onicecandidate = (event) => {
                if (event.candidate) {
                    const candidate = event.candidate.toJSON();
                    if (!candidate.candidate || !candidate.sdpMid || candidate.sdpMLineIndex == null) return;
                    socket.emit('ice-candidate', {
                        from: userId,
                        to: remoteUserId,
                        candidate: candidate,
                        room: roomId
                    });
                }
            };

            peer.oniceconnectionstatechange = () => {
                if (peer.iceConnectionState === 'failed') {
                    peer.restartIce();
                }
            };

            peer.onconnectionstatechange = () => {
                if (peer.connectionState === 'failed' || peer.connectionState === 'disconnected') {
                    peers[remoteUserId]?.close();
                    delete peers[remoteUserId];
                    delete pendingIceCandidates[remoteUserId];
                    delete analyserNodes[remoteUserId];
                    const container = document.getElementById(`video-container-${remoteUserId}`);
                    if (container) container.remove();
                    updateVideoSizes();
                } else if (peer.connectionState === 'connected') {
                    showNotification(`Connected to ${users[remoteUserId]?.username || remoteUserId}`);
                }
            };

            peer.onsignalingstatechange = () => {
                if (peer.signalingState === 'stable' && pendingIceCandidates[remoteUserId]?.length > 0) {
                    const candidates = pendingIceCandidates[remoteUserId].slice();
                    pendingIceCandidates[remoteUserId] = [];
                    candidates.forEach(async candidate => {
                        try {
                            await peer.addIceCandidate(new RTCIceCandidate(candidate));
                        } catch (err) {}
                    });
                }
            };

            return peer;
        }

        document.getElementById('join-room').addEventListener('click', async () => {
            const roomInput = document.getElementById('room-id');
            const usernameInput = document.getElementById('username-input-room');
            const joinButton = document.getElementById('join-room');
            roomId = roomInput.value.trim();
            username = usernameInput.value.trim() || `User ${userId.substring(0, 6)}`;
            if (!roomId) {
                showError('Please enter a valid room ID.');
                return;
            }
            if (!socket.connected) {
                showError('Not connected to the server. Please try again.');
                return;
            }
            try {
                roomInput.disabled = true;
                usernameInput.disabled = true;
                joinButton.disabled = true;
                joinButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Joining...';

                await startVideo();
                await new Promise((resolve, reject) => {
                    socket.emit('join_room', { room: roomId, user_id: userId, username: username }, (response) => {
                        if (response && response.error) {
                            reject(new Error(response.error));
                        } else {
                            resolve();
                        }
                    });
                });

                document.getElementById('room-modal').classList.add('hidden');
                document.getElementById('main-ui').classList.add('active');
                document.getElementById('room-id-display').textContent = `${roomId}`;
                participantCount = 1;
                document.getElementById('participant-count').innerHTML = `<i class="fas fa-users"></i> Participants: ${participantCount}`;
                document.getElementById('participant-count-modal').textContent = participantCount;
                showNotification(`Joined room ${roomId} as ${username}`);
                startVCTimer();
            } catch (err) {
                showError(`Failed to join room: ${err.message}`);
                roomInput.disabled = false;
                usernameInput.disabled = false;
                joinButton.disabled = false;
                joinButton.innerHTML = '<i class="fas fa-sign-in-alt"></i> Join Room';
            }
        });

        document.getElementById('room-id').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') document.getElementById('join-room').click();
        });

        document.getElementById('username-input-room').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') document.getElementById('join-room').click();
        });

        document.getElementById('mute-audio').addEventListener('click', () => {
            isAudioMuted = !isAudioMuted;
            if (localStream) {
                localStream.getAudioTracks().forEach(track => {
                    track.enabled = !isAudioMuted;
                });
            }
            document.getElementById('mute-audio').innerHTML = `<i class="fas fa-microphone${isAudioMuted ? '-slash' : ''}"></i> ${isAudioMuted ? 'Unmute' : 'Mute'} Audio`;
            document.getElementById('mute-audio').classList.toggle('active', isAudioMuted);
            updateMuteIndicators(userId, isAudioMuted, isVideoMuted);
            socket.emit('update_mute_status', { user_id: userId, room: roomId, audioMuted: isAudioMuted, videoMuted: isVideoMuted });
        });

        document.getElementById('mute-video').addEventListener('click', () => {
            isVideoMuted = !isVideoMuted;
            if (localStream) {
                localStream.getVideoTracks().forEach(track => {
                    track.enabled = !isVideoMuted;
                });
            }
            document.getElementById('mute-video').innerHTML = `<i class="fas fa-video${isVideoMuted ? '-slash' : ''}"></i> ${isVideoMuted ? 'Unmute' : 'Mute'} Video`;
            document.getElementById('mute-video').classList.toggle('active', isVideoMuted);
            updateMuteIndicators(userId, isAudioMuted, isVideoMuted);
            socket.emit('update_mute_status', { user_id: userId, room: roomId, audioMuted: isAudioMuted, videoMuted: isVideoMuted });
        });

        document.getElementById('share-screen').addEventListener('click', () => {
            if (isScreenSharing) {
                stopScreenShare();
            } else {
                startScreenShare();
            }
        });

        document.getElementById('record').addEventListener('click', toggleRecording);

        document.getElementById('send-chat').addEventListener('click', () => {
            const input = document.getElementById('chat-input');
            const message = input.value.trim();
            if (message && roomId) {
                const timestamp = new Date().toLocaleTimeString();
                socket.emit('chat_message', { user_id: userId, username: username, message: message, room: roomId, timestamp: timestamp });
                input.value = '';
            }
        });

        document.getElementById('chat-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') document.getElementById('send-chat').click();
        });

        function showChatAlert(message) {
            const chatAlert = document.getElementById('chat-alert');
            chatAlert.textContent = message;
            chatAlert.style.display = 'block';
            setTimeout(() => {
                chatAlert.style.display = 'none';
            }, 5000);
        }

        function addMessageToChat(user_id, username, message, fileId, fileName, timestamp, isNew = false) {
            const chatMessages = document.getElementById('chat-messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message ' + (user_id === userId ? 'sent' : 'received');
            const textSpan = document.createElement('span');
            textSpan.className = 'text';
            if (fileId) {
                textSpan.innerHTML = `${username}: <a href="/download/${fileId}" class="text-blue-300 hover:underline" download="${fileName}">${fileName}</a>`;
            } else {
                textSpan.textContent = `${username}: ${message}`;
            }
            const timeSpan = document.createElement('span');
            timeSpan.className = 'timestamp';
            timeSpan.textContent = timestamp;
            messageDiv.appendChild(textSpan);
            messageDiv.appendChild(timeSpan);
            chatMessages.appendChild(messageDiv);
            if (isNew) {
                messageDiv.style.animation = 'pop 0.3s ease-out';
                setTimeout(() => messageDiv.style.animation = '', 300);
            }
            chatMessages.scrollTop = chatMessages.scrollHeight;
            if (isNew && !isChatVisible && user_id !== userId) {
                unreadMessages++;
                document.getElementById('unread-badge').textContent = unreadMessages;
                document.getElementById('unread-badge').style.display = 'flex';
                showChatAlert(`New message from ${username}: ${message}`);
            }
        }

        socket.on('connect', () => {
            showNotification('Connected to server');
        });

        socket.on('connect_error', () => {
            showError('Failed to connect to server. Retrying...');
        });

        socket.on('disconnect', () => {
            showError('Disconnected from server. Reconnecting...');
            if (!roomId) {
                document.getElementById('room-modal').classList.remove('hidden');
                document.getElementById('main-ui').classList.remove('active');
                document.getElementById('room-id').disabled = false;
                document.getElementById('join-room').disabled = false;
                document.getElementById('join-room').innerHTML = '<i class="fas fa-sign-in-alt"></i> Join Room';
                stopVCTimer();
            }
        });

        socket.on('user_joined', async (data) => {
            if (data.room === roomId) {
                participantCount = data.participant_count;
                document.getElementById('participant-count').innerHTML = `<i class="fas fa-users"></i> Participants: ${participantCount}`;
                document.getElementById('participant-count-modal').textContent = participantCount;
                users[data.user_id] = { 
                    username: data.username, 
                    connection_time: data.connection_time || new Date().toISOString(),
                    audioMuted: false,
                    videoMuted: false
                };
                if (data.user_id !== userId && localStream) {
                    try {
                        showNotification(`Connecting to ${data.username}...`);
                        const peer = createPeer(data.user_id);
                        peers[data.user_id] = peer;
                        const offer = await peer.createOffer({
                            offerToReceiveAudio: true,
                            offerToReceiveVideo: true
                        });
                        await peer.setLocalDescription(offer);
                        socket.emit('offer', { from: userId, to: data.user_id, offer: peer.localDescription, room: roomId });
                    } catch (err) {
                        showError(`Failed to connect to ${data.username}`);
                    }
                }
                updateVideoSizes();
            }
        });

        socket.on('user_left', (data) => {
            if (data.room === roomId) {
                participantCount = data.participant_count;
                document.getElementById('participant-count').innerHTML = `<i class="fas fa-users"></i> Participants: ${participantCount}`;
                document.getElementById('participant-count-modal').textContent = participantCount;
                if (peers[data.user_id]) {
                    peers[data.user_id].close();
                    delete peers[data.user_id];
                    delete pendingIceCandidates[data.user_id];
                    delete analyserNodes[data.user_id];
                    delete users[data.user_id];
                    const container = document.getElementById(`video-container-${data.user_id}`);
                    if (container) container.remove();
                    updateVideoSizes();
                }
            }
        });

        socket.on('offer', async (data) => {
            if (data.to === userId && data.room === roomId) {
                let peer = peers[data.from];
                if (!peer) {
                    peer = createPeer(data.from);
                    peers[data.from] = peer;
                }
                try {
                    if (peer.signalingState !== 'stable') {
                        await peer.setLocalDescription({ type: 'rollback' });
                    }
                    await peer.setRemoteDescription(new RTCSessionDescription(data.offer));
                    const answer = await peer.createAnswer({
                        offerToReceiveAudio: true,
                        offerToReceiveVideo: true
                    });
                    await peer.setLocalDescription(answer);
                    socket.emit('answer', { from: userId, to: data.from, answer: peer.localDescription, room: roomId });
                    if (pendingIceCandidates[data.from]?.length > 0) {
                        for (const candidate of pendingIceCandidates[data.from]) {
                            await peer.addIceCandidate(new RTCIceCandidate(candidate));
                        }
                        pendingIceCandidates[data.from] = [];
                    }
                } catch (err) {
                    showError('WebRTC offer processing failed.');
                }
            }
        });

        socket.on('answer', async (data) => {
            if (data.to === userId && data.room === roomId && peers[data.from]) {
                const peer = peers[data.from];
                try {
                    await peer.setRemoteDescription(new RTCSessionDescription(data.answer));
                    if (pendingIceCandidates[data.from]?.length > 0) {
                        for (const candidate of pendingIceCandidates[data.from]) {
                            await peer.addIceCandidate(new RTCIceCandidate(candidate));
                        }
                        pendingIceCandidates[data.from] = [];
                    }
                } catch (err) {
                    showError('WebRTC answer processing failed.');
                }
            }
        });

        socket.on('ice-candidate', async (data) => {
            if (data.to === userId && data.room === roomId && peers[data.from]) {
                const peer = peers[data.from];
                if (!data.candidate?.candidate || !data.candidate?.sdpMid || data.candidate?.sdpMLineIndex == null) {
                    showError('Invalid ICE candidate received');
                    return;
                }
                const candidate = new RTCIceCandidate(data.candidate);
                try {
                    if (peer.remoteDescription && peer.remoteDescription.type && peer.signalingState === 'stable') {
                        await peer.addIceCandidate(candidate);
                    } else {
                        pendingIceCandidates[data.from].push(candidate);
                    }
                } catch (err) {}
            }
        });

        socket.on('chat_message', (data) => {
            if (data.room === roomId) {
                addMessageToChat(data.user_id, data.username, data.message, null, null, data.timestamp, true);
            }
        });

        socket.on('file_upload', (data) => {
            if (data.room === roomId) {
                addMessageToChat(data.user_id, data.username, null, data.file_id, data.file_name, data.timestamp, true);
            }
        });

        socket.on('update_mute_status', (data) => {
            if (data.room === roomId && users[data.user_id]) {
                users[data.user_id].audioMuted = data.audioMuted;
                users[data.user_id].videoMuted = data.videoMuted;
                updateMuteIndicators(data.user_id, data.audioMuted, data.videoMuted);
            }
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    logger.info("Serving Edge 2 Meet index page")
    server_ip = get_local_ip()
    logger.info(f"Server IP: {server_ip}")
    return render_template_string(INDEX_HTML, server_ip=server_ip)

@app.route('/download/<file_id>')
def download_file(file_id):
    logger.info(f"Download request for file_id: {file_id}")
    cleanup_files()
    if file_id in files:
        try:
            file_data = base64.b64decode(files[file_id]['data'])
            response = make_response(file_data)
            response.headers['Content-Disposition'] = f'attachment; filename="{files[file_id]["name"]}"'
            response.headers['Content-Type'] = 'application/octet-stream'
            return response
        except Exception as e:
            logger.error(f"Error downloading file {file_id}: {str(e)}")
            return 'Error downloading file', 500
    return 'File not found', 404

@socketio.on('join_room')
def handle_join_room(data):
    try:
        logger.info(f"Received join_room data: {data}")
        if not data.get('room') or not data.get('user_id'):
            logger.error(f"Invalid join_room data: {data}")
            emit('error', {'message': 'Missing room or user_id'})
            return
        room = data['room'].strip()
        user_id = data['user_id'].strip()
        username = data.get('username', f"User {user_id[:6]}").strip()
        if not room or not user_id:
            logger.error(f"Empty room or user_id: {data}")
            emit('error', {'message': 'Room ID or user ID cannot be empty'})
            return
        users[user_id] = {
            'sid': request.sid,
            'room': room,
            'username': username,
            'connection_time': datetime.now().isoformat()
        }
        join_room(room)
        if room not in chat_history:
            chat_history[room] = []
        participant_count = sum(1 for u in users.values() if u['room'] == room)
        emit('user_joined', {
            'user_id': user_id,
            'participant_count': participant_count,
            'room': room,
            'username': username,
            'connection_time': users[user_id]['connection_time']
        }, room=room)
        emit('chat_history', chat_history[room], to=request.sid)
        logger.info(f"User {user_id} joined room {room} with username {username}. Total participants: {participant_count}")
    except Exception as e:
        logger.error(f"Error in join_room: {str(e)}")
        emit('error', {'message': f'Failed to join room: {str(e)}'})

@socketio.on('leave_room')
def handle_leave_room(data):
    try:
        user_id = data['user_id']
        room = data['room']
        if user_id in users and users[user_id]['room'] == room:
            del users[user_id]
            participant_count = sum(1 for u in users.values() if u['room'] == room)
            emit('user_left', {'user_id': user_id, 'participant_count': participant_count, 'room': room}, room=room)
            logger.info(f"User {user_id} left room {room}. Total participants: {participant_count}")
    except Exception as e:
        logger.error(f"Error in leave_room: {str(e)}")
        emit('error', {'message': 'Failed to leave room'})

@socketio.on('disconnect')
def handle_disconnect():
    try:
        user_id = None
        room = None
        for uid, info in list(users.items()):
            if info['sid'] == request.sid:
                user_id = uid
                room = info['room']
                break
        if user_id and room:
            del users[user_id]
            participant_count = sum(1 for u in users.values() if u['room'] == room)
            emit('user_left', {'user_id': user_id, 'participant_count': participant_count, 'room': room}, room=room)
            logger.info(f"User {user_id} disconnected from room {room}. Total participants: {participant_count}")
    except Exception as e:
        logger.error(f"Error in disconnect: {str(e)}")

@socketio.on('offer')
def handle_offer(data):
    try:
        emit('offer', data, room=data['room'], include_self=False)
        logger.debug(f"Offer forwarded for room {data['room']} fromsth {data['from']} to {data['to']}")
    except Exception as e:
        logger.error(f"Error in handle_offer: {str(e)}")
        emit('error', {'message': 'Failed to process offer'})

@socketio.on('answer')
def handle_answer(data):
    try:
        emit('answer', data, room=data['room'], include_self=False)
        logger.debug(f"Answer forwarded for room {data['room']} from {data['from']} to {data['to']}")
    except Exception as e:
        logger.error(f"Error in handle_answer: {str(e)}")
        emit('error', {'message': 'Failed to process answer'})

@socketio.on('ice-candidate')
def handle_ice_candidate(data):
    try:
        logger.info(f"Received ICE candidate: {data}")
        required_fields = ['candidate', 'to', 'room', 'from']
        missing_fields = [field for field in required_fields if not data.get(field)]
        if missing_fields:
            logger.error(f"Missing ICE candidate fields: {missing_fields}, data: {data}")
            emit('error', {'message': f'Invalid ICE candidate data: missing {", ".join(missing_fields)}'})
            return
        candidate = data['candidate']
        candidate_fields = ['candidate', 'sdpMid']
        missing_candidate_fields = [field for field in candidate_fields if not candidate.get(field)]
        if missing_candidate_fields or candidate.get('sdpMLineIndex') is None:
            logger.error(f"Malformed ICE candidate, missing: {missing_candidate_fields}, sdpMLineIndex: {candidate.get('sdpMLineIndex')}, candidate: {candidate}")
            emit('error', {'message': f'Malformed ICE candidate: missing {", ".join(missing_candidate_fields) or "sdpMLineIndex"}'})
            return
        emit('ice-candidate', data, room=data['room'], include_self=False)
        logger.debug(f"ICE candidate forwarded for room {data['room']} from {data['from']} to {data['to']}")
    except Exception as e:
        logger.error(f"Error in handle_ice_candidate: {str(e)}")
        emit('error', {'message': f'Failed to process ICE candidate: {str(e)}'})

@socketio.on('chat_message')
def handle_chat_message(data):
    try:
        room = data['room']
        chat_history[room].append({
            'user_id': data['user_id'],
            'username': data['username'],
            'message': data['message'],
            'file_id': data.get('file_id'),
            'file_name': data.get('file_name'),
            'timestamp': data.get('timestamp', datetime.now().strftime("%H:%M:%S"))
        })
        if len(chat_history[room]) > 100:
            chat_history[room].pop(0)
        emit('chat_message', data, room=room)
        logger.debug(f"Chat message in room {room}: {data['username']}: {data['message']} at {data['timestamp']}")
    except Exception as e:
        logger.error(f"Error in handle_chat_message: {str(e)}")
        emit('error', {'message': 'Failed to send chat message'})

@socketio.on('file_upload')
def handle_file_upload(data):
    try:
        room = data['room']
        if len(data['file_data']) > 10 * 1024 * 1024:
            emit('error', {'message': 'File size exceeds 10MB'})
            return
        file_id = str(uuid.uuid4())
        files[file_id] = {
            'name': data['file_name'],
            'data': data['file_data'],
            'timestamp': time.time()
        }
        chat_history[room].append({
            'user_id': data['user_id'],
            'username': users[data['user_id']]['username'],
            'file_id': file_id,
            'file_name': data['file_name'],
            'timestamp': datetime.now().strftime("%H:%M:%S")
        })
        if len(chat_history[room]) > 100:
            chat_history[room].pop(0)
        emit('chat_message', {
            'user_id': data['user_id'],
            'username': users[data['user_id']]['username'],
            'file_id': file_id,
            'file_name': data['file_name'],
            'room': room,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        }, room=room)
        logger.info(f"File uploaded to room {room}: {data['file_name']}")
    except Exception as e:
        logger.error(f"Error in handle_file_upload: {str(e)}")
        emit('error', {'message': 'Failed to upload file'})

@socketio.on('update_mute_status')
def handle_update_mute_status(data):
    try:
        room = data['room']
        user_id = data['user_id']
        audio_muted = data['audioMuted']
        video_muted = data['videoMuted']
        emit('update_mute_status', {
            'user_id': user_id,
            'room': room,
            'audioMuted': audio_muted,
            'videoMuted': video_muted
        }, room=room, include_self=False)
        logger.debug(f"Mute status updated for user {user_id} in room {room}: audio={audio_muted}, video={video_muted}")
    except Exception as e:
        logger.error(f"Error in handle_update_mute_status: {str(e)}")
        emit('error', {'message': 'Failed to update mute status'})

if __name__ == '__main__':
    logger.info("Starting Edge 2 Meet Flask-SocketIO server")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)


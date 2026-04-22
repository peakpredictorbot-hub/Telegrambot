# telegram_predictor_bot.py - VERSIÓN CORREGIDA (AUTO-BET FUNCIONAL)
import json
import os
import threading
import time
import requests
import asyncio
import re
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==================== CONFIGURACIÓN ====================
BOT_TOKEN = "7286614485:AAFv_tLfIJT-qRvlKIwXwVA41eo6cObnuFU"
ADMIN_IDS = [5541162744]
ADMIN_GROUP_ID = -1002513713257
MY_WALLET_BEP20 = "0x621917958C7ac81190e9f876C23D6B9914f31263"

LICENSE_PLANS = {
    "30d": {"price": 10, "days": 30, "type": "single", "name": "📅 30 Días"},
    "6m": {"price": 35, "days": 180, "type": "single", "name": "📅 6 Meses"},
    "1y": {"price": 50, "days": 365, "type": "single", "name": "📅 1 Año"},
    "lifetime_multiuser": {"price": 45, "days": 9999, "type": "multi", "max_users": 5, "name": "👥 Multiuser Lifetime"}
}

# ==================== LICENCIA MANAGER ====================
class LicenseManager:
    def __init__(self, db_file="licenses.json"):
        self.db_file = db_file
        self.licenses = {}
        self.load()
    
    def load(self):
        if os.path.exists(self.db_file):
            with open(self.db_file, 'r') as f:
                self.licenses = json.load(f)
    
    def save(self):
        with open(self.db_file, 'w') as f:
            json.dump(self.licenses, f, indent=2, default=str)
    
    def activate_license(self, user_id: int, plan: str) -> bool:
        if plan not in LICENSE_PLANS:
            return False
        plan_config = LICENSE_PLANS[plan]
        expiry_date = datetime.now() + timedelta(days=plan_config["days"])
        self.licenses[str(user_id)] = {
            "user_id": user_id, "plan": plan, "activated": datetime.now().isoformat(),
            "expiry": expiry_date.isoformat(), "type": plan_config["type"],
            "max_users": plan_config.get("max_users", 1), "active": True
        }
        self.save()
        return True
    
    def check_license(self, user_id: int) -> Dict:
        license_data = self.licenses.get(str(user_id))
        if not license_data:
            return {"valid": False, "reason": "Sin licencia"}
        if not license_data.get("active", False):
            return {"valid": False, "reason": "Licencia inactiva"}
        expiry = datetime.fromisoformat(license_data["expiry"])
        if datetime.now() > expiry:
            license_data["active"] = False
            self.save()
            return {"valid": False, "reason": "Licencia expirada"}
        return {"valid": True, "data": license_data}

# ==================== USER ACCOUNT ====================
class UserAccount:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.token = None
        self.device_id = None
        self.balance = 0.0
        self.logged_in = False
        self.initial_bet = 0.1
        self.current_bet = 0.1
        self.max_consecutive_losses = 5
        self.max_bet = 10.0
        self.consecutive_losses = 0
        self.wins = 0
        self.losses = 0
        self.betting_active = True
        self.use_martingale = False
        self.use_aggressive = False
        self.aggressive_sequence = [0.1, 0.3, 0.7, 1.5, 3.2, 6.5, 13, 26.5, 53.5]
        
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json", 
            "User-Agent": "Mozilla/5.0 (Android 10; Mobile)",
            "Accept-Language": "es-ES,es;q=0.9",
        })
    
    def login(self):
        import random
        self.device_id = ''.join(random.choices('0123456789', k=20))
        url = "https://www.ff2016.vip/api/user/login?lang=es"
        payload = {"account": self.username, "password": self.password, "deviceId": self.device_id}
        try:
            response = self.session.post(url, json=payload, timeout=10)
            data = response.json()
            if data.get("code") == 1:
                self.token = data["data"]["userinfo"]["token"]
                self.session.headers.update({"token": self.token})
                self.logged_in = True
                self.get_balance()
                return True, f"✅ Login OK | Balance: ${self.balance:.2f}"
            return False, data.get("msg", "Error de login")
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def get_balance(self):
        if not self.token:
            return False, "Login first"
        url = "https://www.ff2016.vip/api/user/get_user_info?lang=es"
        payload = {"deviceId": self.device_id}
        try:
            response = self.session.post(url, json=payload, timeout=10)
            data = response.json()
            if data.get("code") == 1:
                self.balance = float(data["data"].get("money", 0.0))
                return True, self.balance
            return False, data.get("msg")
        except Exception as e:
            return False, str(e)
    
    def place_bet(self, side, amount):
        if not self.token:
            return False, "Login first"
        if self.balance < amount:
            return False, f"Saldo insuficiente: ${self.balance:.2f}"
        url = "https://www.ff2016.vip/api/game/add_bet?lang=es"
        payload = {"side": side.lower(), "money": round(float(amount), 2), "redeem_id": 0, "deviceId": self.device_id}
        try:
            response = self.session.post(url, json=payload, timeout=10)
            data = response.json()
            if data.get("code") == 1:
                self.get_balance()
                return True, f"✅ ${amount:.2f} a {side.upper()} | Saldo: ${self.balance:.2f}"
            return False, data.get("msg", "Error en apuesta")
        except Exception as e:
            return False, str(e)
    
    def reset_bet(self):
        self.current_bet = self.initial_bet
        self.consecutive_losses = 0
    
    def update_bet_on_loss(self):
        self.consecutive_losses += 1
        if self.use_martingale:
            new_bet = min(self.current_bet * 2, self.max_bet)
            self.current_bet = new_bet
            return f"Martingale: ${new_bet:.2f}"
        elif self.use_aggressive:
            loss_idx = min(self.consecutive_losses - 1, len(self.aggressive_sequence) - 1)
            new_bet = min(self.aggressive_sequence[loss_idx], self.max_bet)
            self.current_bet = new_bet
            return f"Aggressive: ${new_bet:.2f}"
        else:
            return f"Same bet: ${self.current_bet:.2f}"

# ==================== PATTERN DETECTOR ====================
class PatternDetector:
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.waiting_for_pattern = True
        self.using_base_logic = False
        self.last_pattern_used = None
        self.consecutive_wins = 0
        self.rounds_since_loss = 0
    
    def detect_pattern(self, history):
        if len(history) < 2:
            return None
        
        history_list = list(history)
        
        if len(history_list) >= 3:
            if history_list[-3] == 'blue' and history_list[-2] == 'red' and history_list[-1] == 'red':
                return 'blue'
            if history_list[-3] == 'red' and history_list[-2] == 'blue' and history_list[-1] == 'blue':
                return 'red'
        
        if len(history_list) >= 5:
            if (history_list[-5] == 'blue' and history_list[-4] == 'red' and 
                history_list[-3] == 'red' and history_list[-2] == 'red' and history_list[-1] == 'red'):
                return 'red'
            if (history_list[-5] == 'red' and history_list[-4] == 'blue' and 
                history_list[-3] == 'blue' and history_list[-2] == 'blue' and history_list[-1] == 'blue'):
                return 'blue'
        
        if len(history_list) >= 3:
            if history_list[-3] == 'blue' and history_list[-2] == 'red' and history_list[-1] == 'blue':
                return 'red'
            if history_list[-3] == 'red' and history_list[-2] == 'blue' and history_list[-1] == 'red':
                return 'blue'
        
        if len(history_list) >= 5:
            if (history_list[-5] == 'blue' and history_list[-4] == 'red' and 
                history_list[-3] == 'blue' and history_list[-2] == 'red' and history_list[-1] == 'red'):
                return 'blue'
            if (history_list[-5] == 'red' and history_list[-4] == 'blue' and 
                history_list[-3] == 'red' and history_list[-2] == 'blue' and history_list[-1] == 'blue'):
                return 'red'
        
        if len(history_list) >= 2:
            if history_list[-2] == 'blue' and history_list[-1] == 'blue':
                return 'red'
            if history_list[-2] == 'red' and history_list[-1] == 'red':
                return 'blue'
        
        return None

# ==================== BASE LOGIC ====================
class BaseLogic:
    def __init__(self):
        self.last_pattern_used = None
    
    def get_prediction(self, history):
        if not history:
            return None
        
        last_color = history[-1]
        
        if len(history) >= 3:
            if (history[-3] != history[-2] and 
                history[-2] != history[-1] and 
                history[-3] == history[-1]):
                return 'blue' if last_color == 'red' else 'red'
        
        if len(history) >= 2 and self.last_pattern_used != "double":
            if history[-2] == history[-1]:
                self.last_pattern_used = "double"
                return 'blue' if last_color == 'red' else 'red'
        
        self.last_pattern_used = None
        return last_color

# ==================== USER PREDICTOR ====================
class UserPredictor:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.pattern_detector = PatternDetector()
        self.base_logic = BaseLogic()
        self.session_history = deque(maxlen=50)
        self.last_prediction = None
        self.waiting_for_pattern = True
        self.waiting_rounds = 0
        self.min_wait_rounds = 1
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.total_wins = 0
        self.total_losses = 0
        self.active = True
        self.on_status = None
        self.on_prediction = None
        self.on_result = None
    
    def _get_historial_str(self):
        last_colors = list(self.session_history)[-5:]
        history_emojis = []
        for c in last_colors:
            history_emojis.append("🔴" if c == 'red' else "🔵")
        return ''.join(history_emojis) if history_emojis else ""
    
    def _get_estado_str(self):
        if self.last_prediction is not None:
            return "Esperando resultado"
        elif self.waiting_rounds > 0:
            return f"Esperando {self.waiting_rounds} ronda"
        elif self.waiting_for_pattern:
            return "Buscando patrón"
        else:
            return "Lógica base"
    
    def process_color(self, color: str):
        if not self.active:
            return
        
        self.session_history.append(color)
        
        # Enviar mensaje de historial + estado
        if self.on_status:
            historial = self._get_historial_str()
            estado = self._get_estado_str()
            color_emoji = "🔴" if color == 'red' else "🔵"
            color_text = "ROJO" if color == 'red' else "AZUL"
            self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\nEstado: {estado}")
        
        if self.waiting_rounds > 0:
            self.waiting_rounds -= 1
            return
        
        if self.last_prediction is None:
            self._make_prediction()
        else:
            self._verify_prediction(color)
    
    def _make_prediction(self):
        if len(self.session_history) < 2:
            return
        
        prediction = None
        source = ""
        
        if self.waiting_for_pattern:
            prediction = self.pattern_detector.detect_pattern(self.session_history)
            if prediction:
                self.waiting_for_pattern = False
                source = "PATRÓN"
        else:
            prediction = self.base_logic.get_prediction(list(self.session_history))
            source = "LÓGICA BASE"
        
        if prediction:
            self.last_prediction = prediction
            pred_emoji = "🔴" if prediction == 'red' else "🔵"
            pred_text = "ROJO" if prediction == 'red' else "AZUL"
            
            # Enviar mensaje de SEÑAL
            if self.on_prediction:
                self.on_prediction(f"SEÑAL {pred_emoji}\n{source}")
    
    def _verify_prediction(self, actual_color):
        if self.last_prediction is None:
            return
        
        is_correct = (self.last_prediction == actual_color)
        actual_emoji = "🔴" if actual_color == 'red' else "🔵"
        
        if is_correct:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.total_wins += 1
            self.waiting_for_pattern = False
            self.waiting_rounds = 0
            
            # Enviar mensaje de WIN
            if self.on_result:
                self.on_result(f"✅ WIN\nRacha: {self.consecutive_wins}", True)
            
            # Limpiar predicción y generar nueva inmediatamente
            self.last_prediction = None
            self._make_prediction()
            
            # Enviar nuevo historial después de la predicción
            if self.on_status:
                historial = self._get_historial_str()
                estado = self._get_estado_str()
                last_color = self.session_history[-1] if self.session_history else None
                if last_color:
                    color_emoji = "🔴" if last_color == 'red' else "🔵"
                    color_text = "ROJO" if last_color == 'red' else "AZUL"
                    self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\nEstado: {estado}")
            
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.total_losses += 1
            self.waiting_for_pattern = True
            self.waiting_rounds = self.min_wait_rounds
            
            # Enviar mensaje de LOSS
            if self.on_result:
                self.on_result(f"❌ LOSS\nRacha: {self.consecutive_losses}", False)
            
            self.last_prediction = None

# ==================== POLLING GLOBAL CON RECONEXIÓN ====================
class GlobalPolling:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.user_predictors: Dict[int, UserPredictor] = {}
        self.running = False
        self.last_processed_index = 0
        self.last_color_time = time.time()
        self.api_url = "https://www.ff2016.vip/api/game/getchart?lang=es"
        self.headers = {
            "token": "81c635fe-0f6e-4bff-aede-4a69d9c9ef2d",
            "Content-Type": "application/json"
        }
        self._lock = threading.Lock()
        self.reconnect_timeout = 90
    
    def register_user(self, user_id: int, on_status=None, on_prediction=None, on_result=None) -> UserPredictor:
        with self._lock:
            if user_id not in self.user_predictors:
                predictor = UserPredictor(user_id)
                predictor.on_status = on_status
                predictor.on_prediction = on_prediction
                predictor.on_result = on_result
                self.user_predictors[user_id] = predictor
                print(f"✅ Usuario {user_id} registrado")
            return self.user_predictors[user_id]
    
    def unregister_user(self, user_id: int):
        with self._lock:
            if user_id in self.user_predictors:
                self.user_predictors[user_id].active = False
                del self.user_predictors[user_id]
                print(f"❌ Usuario {user_id} eliminado")
    
    def start(self):
        if self.running:
            return
        self.running = True
        self.last_color_time = time.time()
        threading.Thread(target=self._polling_loop, daemon=True).start()
        print("🌍 Polling global iniciado")
    
    def stop(self):
        self.running = False
    
    def _reconnect(self):
        print("⚠️ Reconectando a la API...")
        self.last_processed_index = 0
        self.last_color_time = time.time()
    
    def _polling_loop(self):
        while self.running:
            try:
                if time.time() - self.last_color_time > self.reconnect_timeout:
                    self._reconnect()
                
                response = requests.post(self.api_url, headers=self.headers, timeout=10)
                if response.ok:
                    data = response.json()
                    if data.get('code') == 1:
                        all_colors = data['data']['ori']
                        if len(all_colors) > self.last_processed_index:
                            new_colors = all_colors[self.last_processed_index:]
                            self.last_processed_index = len(all_colors)
                            last_color = new_colors[-1].lower()
                            self.last_color_time = time.time()
                            print(f"🎨 Nuevo color: {last_color}")
                            with self._lock:
                                for user_id, predictor in self.user_predictors.items():
                                    if predictor.active:
                                        predictor.process_color(last_color)
            except Exception as e:
                print(f"❌ Error polling: {e}")
            time.sleep(2)

# ==================== BOT DE TELEGRAM ====================
class PredictionBot:
    def __init__(self, token: str):
        self.token = token
        self.license_manager = LicenseManager()
        self.global_polling = GlobalPolling()
        self.user_sessions: Dict[int, Dict] = {}
        self.pending_payments: Dict[int, Dict] = {}
        self.application = None
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    async def _send_message(self, user_id: int, text: str, parse_mode: str = 'Markdown'):
        try:
            if self.application:
                await self.application.bot.send_message(
                    chat_id=user_id, 
                    text=text, 
                    parse_mode=parse_mode,
                    read_timeout=15,
                    write_timeout=15,
                    connect_timeout=15
                )
        except Exception as e:
            print(f"❌ Error enviando mensaje: {e}")
    
    def _sync_send_message(self, user_id: int, text: str, parse_mode: str = 'Markdown'):
        if self.application:
            asyncio.run_coroutine_threadsafe(self._send_message(user_id, text, parse_mode), self.loop)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        
        if not license_check['valid']:
            keyboard = [[InlineKeyboardButton("💰 Comprar Licencia", callback_data='buy_license')]]
            await update.message.reply_text(
                "🔒 *ACCESO RESTRINGIDO*\n\nNo tienes licencia activa.\n\n"
                "💰 *PRECIOS:*\n• 30 días: 10 USDT\n• 6 meses: 35 USDT\n• 1 año: 50 USDT\n• Multiuser Lifetime: 45 USDT\n\n"
                "Usa /comprar para obtener una.",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("📡 MODO SEÑALES", callback_data='signals_mode')],
            [InlineKeyboardButton("🤖 MODO AUTOMÁTICO", callback_data='auto_mode')],
            [InlineKeyboardButton("📜 Info Licencia", callback_data='license_info')],
            [InlineKeyboardButton("💰 Comprar Licencia", callback_data='buy_license')]
        ]
        await update.message.reply_text(
            f"🎰 *PREDICTOR PRO BOT*\n\n✅ Licencia: {license_check['data']['plan']}\n"
            f"👥 Tipo: {license_check['data']['type']}\n\nSelecciona un modo:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
    
    async def signals_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        
        if not self.global_polling.running:
            self.global_polling.start()
        
        def on_status(msg):
            self._sync_send_message(user_id, msg)
        
        def on_prediction(msg):
            self._sync_send_message(user_id, msg)
        
        def on_result(msg, is_win):
            self._sync_send_message(user_id, msg, parse_mode=None)
        
        self.global_polling.register_user(user_id, on_status, on_prediction, on_result)
        self.user_sessions[user_id] = {'mode': 'signals'}
        
        await query.edit_message_text(
            "📡 *MODO SEÑALES ACTIVADO*\n\n"
            "Recibirás mensajes con el formato:\n"
            "🔴/🔵 + Historial + Estado\n"
            "SEÑAL + color\n"
            "WIN/LOSS + racha\n\n"
            "Usa /stop para detener.",
            parse_mode='Markdown'
        )
    
    async def auto_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        max_accounts = license_check['data'].get('max_users', 1)
        
        await query.edit_message_text(
            f"🤖 *MODO AUTOMÁTICO*\n\n👥 Máximo de cuentas: {max_accounts}\n\n"
            "Envía tus credenciales:\n`usuario:contraseña`\n\n"
            f"{'Para varias: `user1:pass1,user2:pass2`' if max_accounts > 1 else ''}\n\n"
            "Ejemplo: `juan123:miPass123`\n\n"
            "⚠️ *IMPORTANTE:* Después del login, deberás CONFIGURAR las apuestas.",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_credentials'] = True
    
    async def process_credentials(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        license_check = self.license_manager.check_license(user_id)
        max_accounts = license_check['data'].get('max_users', 1)
        
        accounts_data = []
        if ',' in text and max_accounts > 1:
            for cred in text.split(','):
                if ':' in cred:
                    u, p = cred.strip().split(':', 1)
                    accounts_data.append((u, p))
        elif ':' in text:
            u, p = text.strip().split(':', 1)
            accounts_data.append((u, p))
        else:
            await update.message.reply_text("❌ Formato incorrecto. Usa: usuario:contraseña")
            return
        
        if len(accounts_data) > max_accounts:
            await update.message.reply_text(f"❌ Máximo {max_accounts} cuentas")
            return
        
        accounts = []
        for username, password in accounts_data:
            acc = UserAccount(username, password)
            success, msg = acc.login()
            if success:
                accounts.append(acc)
                await update.message.reply_text(f"✅ {username}: ${acc.balance:.2f}")
            else:
                await update.message.reply_text(f"❌ {username}: {msg}")
        
        if accounts:
            if not self.global_polling.running:
                self.global_polling.start()
            
            def on_status(msg):
                self._sync_send_message(user_id, msg)
            
            def on_prediction(msg):
                self._sync_send_message(user_id, msg)
                # Verificar si auto-bet está activado
                if self.user_sessions.get(user_id, {}).get('auto_betting_active'):
                    # Extraer color del mensaje "SEÑAL 🔴" o "SEÑAL 🔵"
                    if '🔴' in msg:
                        color = 'red'
                    elif '🔵' in msg:
                        color = 'blue'
                    else:
                        color = None
                    
                    if color:
                        print(f"🎯 Auto-bet: apostando a {color}")
                        self._execute_bets(user_id, color)
            
            def on_result(msg, is_win):
                self._sync_send_message(user_id, msg, parse_mode=None)
                if self.user_sessions.get(user_id, {}).get('auto_betting_active'):
                    self._update_bet_on_result(user_id, is_win)
                    self._show_balances(user_id)
            
            self.global_polling.register_user(user_id, on_status, on_prediction, on_result)
            
            self.user_sessions[user_id] = {
                'mode': 'auto',
                'accounts': accounts,
                'auto_betting_active': False,
                'bet_config': {
                    'initial_bet': 0.1,
                    'current_bet': 0.1,
                    'max_bet': 10.0,
                    'max_losses': 5,
                    'use_martingale': False,
                    'use_aggressive': False
                }
            }
            await self.show_betting_config(update, user_id)
        
        context.user_data['awaiting_credentials'] = False
    
    def _execute_bets(self, user_id: int, color: str):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        
        print(f"💰 Ejecutando apuestas para usuario {user_id} - Color: {color}")
        
        for account in session.get('accounts', []):
            print(f"   Cuenta: {account.username} - Activa: {account.betting_active} - Balance: ${account.balance} - Apuesta: ${account.current_bet}")
            
            if not account.betting_active:
                continue
            if account.balance <= 0:
                msg = f"⚠️ {account.username}: Sin fondos (${account.balance})"
                self._sync_send_message(user_id, msg)
                account.betting_active = False
                continue
            if account.current_bet > account.balance:
                msg = f"⚠️ {account.username}: Apuesta ${account.current_bet:.2f} > Balance ${account.balance:.2f}"
                self._sync_send_message(user_id, msg)
                continue
            
            success, msg = account.place_bet(color, account.current_bet)
            if success:
                self._sync_send_message(user_id, f"✅ {account.username}: ${account.current_bet:.2f} a {color.upper()}")
            else:
                self._sync_send_message(user_id, f"❌ {account.username}: {msg}")
    
    def _update_bet_on_result(self, user_id: int, won: bool):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        
        print(f"📊 Actualizando apuestas - Usuario {user_id} - {'WIN' if won else 'LOSS'}")
        
        for account in session.get('accounts', []):
            if not account.betting_active:
                continue
            
            if won:
                account.wins += 1
                account.reset_bet()
                self._sync_send_message(user_id, f"💰 {account.username}: Apuesta reiniciada a ${account.current_bet:.2f}")
            else:
                account.losses += 1
                if account.consecutive_losses + 1 >= account.max_consecutive_losses:
                    self._sync_send_message(user_id, f"🛑 {account.username}: Stop loss alcanzado")
                    account.betting_active = False
                else:
                    msg = account.update_bet_on_loss()
                    self._sync_send_message(user_id, f"📉 {account.username}: {msg}")
    
    def _show_balances(self, user_id: int):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        msg = "💰 *SALDOS ACTUALIZADOS*\n\n"
        for acc in session.get('accounts', []):
            acc.get_balance()
            msg += f"• {acc.username}: ${acc.balance:.2f}\n"
        self._sync_send_message(user_id, msg)
    
    async def show_betting_config(self, update, user_id):
        session = self.user_sessions[user_id]
        config = session['bet_config']
        
        keyboard = [
            [InlineKeyboardButton(f"💰 Inicial: ${config['initial_bet']}", callback_data='cfg_initial')],
            [InlineKeyboardButton(f"📈 Máximo: ${config['max_bet']}", callback_data='cfg_max_bet')],
            [InlineKeyboardButton(f"🛑 Max Losses: {config['max_losses']}", callback_data='cfg_max_losses')],
            [InlineKeyboardButton(f"🎲 Martingala: {'✅' if config['use_martingale'] else '❌'}", callback_data='cfg_martingale')],
            [InlineKeyboardButton(f"⚡ Agresiva: {'✅' if config['use_aggressive'] else '❌'}", callback_data='cfg_aggressive')],
            [InlineKeyboardButton("📊 Ver Balances", callback_data='view_balances')],
            [InlineKeyboardButton("▶️ INICIAR AUTO-BET", callback_data='start_autobet')],
            [InlineKeyboardButton("◀️ Volver", callback_data='back_to_start')]
        ]
        
        msg = (
            f"⚙️ *CONFIGURACIÓN*\n\n"
            f"💰 Apuesta actual: ${config['current_bet']}\n"
            f"🎲 Martingala: {'✅' if config['use_martingale'] else '❌'}\n"
            f"⚡ Agresiva: {'✅' if config['use_aggressive'] else '❌'}\n\n"
            f"Configura y presiona INICIAR"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    async def cfg_initial(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("💰 Envía nuevo monto inicial (mínimo 0.1):\nEj: `0.5`", parse_mode='Markdown')
        context.user_data['awaiting_initial_bet'] = True
    
    async def cfg_max_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("📈 Envía monto máximo:\nEj: `10.0`", parse_mode='Markdown')
        context.user_data['awaiting_max_bet'] = True
    
    async def cfg_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🛑 Envía número máximo de pérdidas:\nEj: `5`", parse_mode='Markdown')
        context.user_data['awaiting_max_losses'] = True
    
    async def cfg_martingale(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        current = self.user_sessions[user_id]['bet_config']['use_martingale']
        self.user_sessions[user_id]['bet_config']['use_martingale'] = not current
        if not current:
            self.user_sessions[user_id]['bet_config']['use_aggressive'] = False
        await update.callback_query.answer(f"Martingala {'activada' if not current else 'desactivada'}")
        await self.show_betting_config(update, user_id)
    
    async def cfg_aggressive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        current = self.user_sessions[user_id]['bet_config']['use_aggressive']
        self.user_sessions[user_id]['bet_config']['use_aggressive'] = not current
        if not current:
            self.user_sessions[user_id]['bet_config']['use_martingale'] = False
        await update.callback_query.answer(f"Modo agresivo {'activado' if not current else 'desactivado'}")
        await self.show_betting_config(update, user_id)
    
    async def process_initial_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        try:
            amount = float(update.message.text)
            if amount < 0.1:
                await update.message.reply_text("❌ Mínimo 0.1")
                return
            self.user_sessions[user_id]['bet_config']['initial_bet'] = amount
            self.user_sessions[user_id]['bet_config']['current_bet'] = amount
            for acc in self.user_sessions[user_id]['accounts']:
                acc.initial_bet = amount
                acc.current_bet = amount
            await update.message.reply_text(f"✅ Monto inicial: ${amount:.2f}")
            await self.show_betting_config(update, user_id)
        except:
            await update.message.reply_text("❌ Número inválido")
        context.user_data['awaiting_initial_bet'] = False
    
    async def process_max_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        try:
            amount = float(update.message.text)
            if amount < 0.1:
                await update.message.reply_text("❌ Mínimo 0.1")
                return
            self.user_sessions[user_id]['bet_config']['max_bet'] = amount
            for acc in self.user_sessions[user_id]['accounts']:
                acc.max_bet = amount
            await update.message.reply_text(f"✅ Máximo: ${amount:.2f}")
            await self.show_betting_config(update, user_id)
        except:
            await update.message.reply_text("❌ Número inválido")
        context.user_data['awaiting_max_bet'] = False
    
    async def process_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        try:
            value = int(update.message.text)
            if value < 1:
                await update.message.reply_text("❌ Mínimo 1")
                return
            if value > 20:
                await update.message.reply_text("❌ Máximo 20")
                return
            self.user_sessions[user_id]['bet_config']['max_losses'] = value
            for acc in self.user_sessions[user_id]['accounts']:
                acc.max_consecutive_losses = value
            await update.message.reply_text(f"✅ Max Losses: {value}")
            await self.show_betting_config(update, user_id)
        except:
            await update.message.reply_text("❌ Número inválido")
        context.user_data['awaiting_max_losses'] = False
    
    async def start_autobet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_sessions:
            await update.callback_query.answer("❌ No hay cuentas")
            return
        
        config = self.user_sessions[user_id]['bet_config']
        
        for acc in self.user_sessions[user_id]['accounts']:
            acc.initial_bet = config['initial_bet']
            acc.current_bet = config['initial_bet']
            acc.max_bet = config['max_bet']
            acc.max_consecutive_losses = config['max_losses']
            acc.use_martingale = config['use_martingale']
            acc.use_aggressive = config['use_aggressive']
            acc.consecutive_losses = 0
            acc.betting_active = True
        
        self.user_sessions[user_id]['auto_betting_active'] = True
        
        await update.callback_query.edit_message_text(
            f"✅ *AUTO-BET ACTIVADO*\n\n"
            f"💰 Inicial: ${config['initial_bet']}\n"
            f"📈 Máximo: ${config['max_bet']}\n"
            f"🛑 Max Losses: {config['max_losses']}\n"
            f"🎲 Martingala: {'✅' if config['use_martingale'] else '❌'}\n"
            f"⚡ Agresiva: {'✅' if config['use_aggressive'] else '❌'}\n\n"
            f"Usa /stop para detener.",
            parse_mode='Markdown'
        )
    
    async def view_balances(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        session = self.user_sessions.get(user_id)
        if not session:
            await update.callback_query.answer("No hay sesión")
            return
        msg = "💰 *BALANCES*\n\n"
        for acc in session.get('accounts', []):
            acc.get_balance()
            msg += f"• {acc.username}: ${acc.balance:.2f}\n"
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    
    async def buy_license(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton(f"{p['name']} - {p['price']} USDT", callback_data=f'plan_{pid}')] for pid, p in LICENSE_PLANS.items()]
        await update.callback_query.edit_message_text(
            "💰 *COMPRAR LICENCIA*\n\nSelecciona un plan:",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown'
        )
    
    async def select_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        plan_id = query.data.replace('plan_', '')
        plan = LICENSE_PLANS[plan_id]
        user_id = update.effective_user.id
        
        self.pending_payments[user_id] = {
            'plan': plan_id,
            'amount': plan['price'],
            'username': update.effective_user.username or update.effective_user.first_name,
            'user_id': user_id
        }
        
        keyboard = [
            [InlineKeyboardButton("📸 Enviar Comprobante", callback_data='send_payment_proof')],
            [InlineKeyboardButton("◀️ Volver", callback_data='back_to_start')]
        ]
        
        await query.edit_message_text(
            f"💸 *PAGO REQUERIDO*\n\n"
            f"📦 Plan: {plan['name']}\n"
            f"💰 Monto: {plan['price']} USDT\n"
            f"🔗 Red: BEP20\n\n"
            f"📤 *Wallet:*\n`{MY_WALLET_BEP20}`\n\n"
            f"1️⃣ Transferir {plan['price']} USDT\n"
            f"2️⃣ Toca 📸 Enviar Comprobante\n"
            f"3️⃣ Adjunta CAPTURA\n\n"
            f"🆔 ID: `{user_id}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def send_payment_proof(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        
        if user_id not in self.pending_payments:
            await query.edit_message_text("❌ No hay compra pendiente.")
            return
        
        await query.edit_message_text(
            "📸 *ENVÍA CAPTURA*\n\n"
            "Envía la imagen con el TXID",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_payment_proof'] = True
    
    async def handle_payment_proof(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in self.pending_payments:
            await update.message.reply_text("❌ No hay compra pendiente.")
            context.user_data['awaiting_payment_proof'] = False
            return
        
        plan_info = self.pending_payments[user_id]
        plan_name = LICENSE_PLANS[plan_info['plan']]['name']
        amount = plan_info['amount']
        username = update.effective_user.username or update.effective_user.first_name
        
        txid = "No especificado"
        caption = update.message.caption or ""
        txid_match = re.search(r'(0x[a-fA-F0-9]{64}|TXID[:\s]*([a-fA-F0-9]{64}))', caption, re.IGNORECASE)
        if txid_match:
            txid = txid_match.group(0)
        
        admin_msg = (
            f"🆕 *NUEVO PAGO*\n\n"
            f"👤 @{username}\n"
            f"🆔 `{user_id}`\n"
            f"📦 {plan_name}\n"
            f"💰 {amount} USDT\n"
            f"📝 `{txid}`\n\n"
            f"✅ `/validar {user_id} {plan_info['plan']}`"
        )
        
        try:
            if update.message.photo:
                photo = update.message.photo[-1]
                await self.application.bot.send_photo(
                    chat_id=ADMIN_GROUP_ID,
                    photo=photo.file_id,
                    caption=admin_msg,
                    parse_mode='Markdown'
                )
            elif update.message.document:
                doc = update.message.document
                await self.application.bot.send_document(
                    chat_id=ADMIN_GROUP_ID,
                    document=doc.file_id,
                    caption=admin_msg,
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Envía una imagen")
                return
            
            await update.message.reply_text("✅ Comprobante enviado. Será verificado.")
            del self.pending_payments[user_id]
            context.user_data['awaiting_payment_proof'] = False
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    async def license_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        if license_check['valid']:
            data = license_check['data']
            expiry = datetime.fromisoformat(data['expiry'])
            days = (expiry - datetime.now()).days
            await update.callback_query.edit_message_text(
                f"📜 *LICENCIA*\n\nPlan: {data['plan']}\n"
                f"Expira: {expiry.strftime('%Y-%m-%d')}\n"
                f"Días: {days if days < 3650 else '∞'}",
                parse_mode='Markdown'
            )
        else:
            await update.callback_query.edit_message_text("❌ Sin licencia activa")
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_sessions:
            self.user_sessions[user_id]['auto_betting_active'] = False
            self.global_polling.unregister_user(user_id)
            del self.user_sessions[user_id]
        await update.message.reply_text("⏹️ Auto-bet detenido. Usa /start")
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("❌ Cancelado")
    
    async def validate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ No autorizado")
            return
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("Uso: /validar USER_ID PLAN\nPlanes: 30d, 6m, 1y, lifetime_multiuser")
            return
        user_id = int(args[0])
        plan = args[1]
        if self.license_manager.activate_license(user_id, plan):
            await update.message.reply_text(f"✅ Licencia {plan} activada para {user_id}")
            await self._send_message(user_id, f"🎉 ¡Licencia activada! Usa /start")
        else:
            await update.message.reply_text("❌ Plan inválido")
    
    async def back_to_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.start_command(update, context)
    
    async def handle_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('awaiting_credentials'):
            await self.process_credentials(update, context)
        elif context.user_data.get('awaiting_initial_bet'):
            await self.process_initial_bet(update, context)
        elif context.user_data.get('awaiting_max_bet'):
            await self.process_max_bet(update, context)
        elif context.user_data.get('awaiting_max_losses'):
            await self.process_max_losses(update, context)
        elif context.user_data.get('awaiting_payment_proof'):
            if update.message.photo or update.message.document:
                await self.handle_payment_proof(update, context)
            else:
                await update.message.reply_text("❌ Envía una CAPTURA")
    
    def run(self):
        self.application = Application.builder().token(self.token).build()
        
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))
        self.application.add_handler(CommandHandler("validar", self.validate_command))
        
        self.application.add_handler(CallbackQueryHandler(self.signals_mode, pattern='signals_mode'))
        self.application.add_handler(CallbackQueryHandler(self.auto_mode, pattern='auto_mode'))
        self.application.add_handler(CallbackQueryHandler(self.buy_license, pattern='buy_license'))
        self.application.add_handler(CallbackQueryHandler(self.select_plan, pattern='plan_'))
        self.application.add_handler(CallbackQueryHandler(self.send_payment_proof, pattern='send_payment_proof'))
        self.application.add_handler(CallbackQueryHandler(self.cfg_initial, pattern='cfg_initial'))
        self.application.add_handler(CallbackQueryHandler(self.cfg_max_bet, pattern='cfg_max_bet'))
        self.application.add_handler(CallbackQueryHandler(self.cfg_max_losses, pattern='cfg_max_losses'))
        self.application.add_handler(CallbackQueryHandler(self.cfg_martingale, pattern='cfg_martingale'))
        self.application.add_handler(CallbackQueryHandler(self.cfg_aggressive, pattern='cfg_aggressive'))
        self.application.add_handler(CallbackQueryHandler(self.start_autobet, pattern='start_autobet'))
        self.application.add_handler(CallbackQueryHandler(self.view_balances, pattern='view_balances'))
        self.application.add_handler(CallbackQueryHandler(self.license_info, pattern='license_info'))
        self.application.add_handler(CallbackQueryHandler(self.back_to_start, pattern='back_to_start'))
        
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_messages))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_messages))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_messages))
        
        print("=" * 50)
        print("🤖 PREDICTOR PRO BOT INICIADO")
        print("=" * 50)
        print(f"👑 Admin ID: {ADMIN_IDS[0]}")
        print("=" * 50)
        
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    print("🚀 INICIANDO PREDICTOR PRO BOT...")
    bot = PredictionBot(BOT_TOKEN)
    bot.run()
# bot.py - PREDICTOR PRO BOT (MODO PEAK-BREAK CON 3 LOSS + ESPERA 1 RONDA)
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
    "test_24h": {"price": 0, "days": 1, "type": "single", "name": "🎁 Prueba 24h (GRATIS)"},
    "30d": {"price": 10, "days": 30, "type": "single", "name": "📅 30 Días"},
    "6m": {"price": 35, "days": 180, "type": "single", "name": "📅 6 Meses"},
    "1y": {"price": 50, "days": 365, "type": "single", "name": "📅 1 Año"},
    "lifetime_multiuser": {"price": 45, "days": 9999, "type": "multi", "max_users": 5, "name": "👥 Multiuser Lifetime"},
    "peakbreak_30d": {"price": 15, "days": 30, "type": "peakbreak", "name": "📊 Peak-Break 30 Días"},
    "peakbreak_lifetime": {"price": 60, "days": 9999, "type": "peakbreak", "name": "📊 Peak-Break Lifetime"},
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

# ==================== USER ACCOUNT (BASE) ====================
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


# ==================== PEAK-BREAK ACCOUNT (CORREGIDO - 3 LOSS + ESPERA 1 RONDA) ====================
class PeakBreakAccount(UserAccount):
    """
    Cuenta especial para el modo Peak-Break.
    - Solo apuesta despues de 3 LOSS seguidos
    - Despues de CADA LOSS (activo o inactivo), espera 1 ronda
    """
    def __init__(self, username, password):
        super().__init__(username, password)
        self.peakbreak_consecutive_losses = 0  # Contador de LOSS para activacion
        self.peakbreak_active = False  # ¿Estamos en modo apostando?
        self.PEAKBREAK_THRESHOLD = 3
        self.wait_rounds = 0  # ← NUEVO: rondas de espera despues de cada LOSS
    
    def reset_peakbreak(self):
        """Reset despues de un WIN - volvemos a esperar 3 LOSS"""
        self.peakbreak_consecutive_losses = 0
        self.peakbreak_active = False
        self.wait_rounds = 0
        print(f"[PEAK] {self.username}: Reset - esperando {self.PEAKBREAK_THRESHOLD} LOSS")
    
    def record_loss(self):
        """
        Registrar una perdida.
        RETORNA: True si estaba activo (perdio apostando), False si no
        """
        # Despues de CADA LOSS, esperar 1 ronda
        self.wait_rounds = 1
        print(f"[PEAK] {self.username}: Esperara 1 ronda despues de este LOSS")
        
        if not self.peakbreak_active:
            # Estamos esperando - contar LOSS
            self.peakbreak_consecutive_losses += 1
            print(f"[PEAK] {self.username}: LOSS #{self.peakbreak_consecutive_losses}/{self.PEAKBREAK_THRESHOLD} (esperando)")
            
            if self.peakbreak_consecutive_losses >= self.PEAKBREAK_THRESHOLD:
                # ¡Se activa el modo!
                self.peakbreak_active = True
                self.peakbreak_consecutive_losses = 0
                self.betting_active = True
                self.wait_rounds = 0  # Al activarse, no espera (apuesta de inmediato)
                print(f"[PEAK] {self.username}: ✅ ACTIVADO! Apostara en la proxima señal")
            return False  # No estaba activo
        else:
            # Ya estamos activos - es una perdida en la racha de apuestas
            print(f"[PEAK] {self.username}: LOSS en modo activo (racha: {self.consecutive_losses + 1})")
            return True  # Estaba activo y perdio
    
    def record_win(self):
        """Registrar una ganancia - resetea todo el Peak-Break"""
        self.reset_peakbreak()
        print(f"[PEAK] {self.username}: WIN - Peak-Break reseteado")
    
    def should_bet(self) -> bool:
        """¿Debemos apostar en esta ronda?"""
        # NO apostar si estamos en ronda de espera
        if self.wait_rounds > 0:
            print(f"[PEAK] {self.username}: En espera ({self.wait_rounds} ronda/s restantes)")
            return False
        return self.peakbreak_active
    
    def decrement_wait(self):
        """Decrementar contador de espera (llamar al inicio de cada ronda)"""
        if self.wait_rounds > 0:
            self.wait_rounds -= 1
            if self.wait_rounds == 0:
                print(f"[PEAK] {self.username}: Espera terminada - listo para apostar si esta activo")
    
    def get_status(self) -> str:
        """Obtener estado para mostrar"""
        if self.wait_rounds > 0:
            return f"⏳ Esperando {self.wait_rounds} ronda(s) por LOSS"
        if self.peakbreak_active:
            return "⚡ ACTIVO (apostando)"
        else:
            remaining = self.PEAKBREAK_THRESHOLD - self.peakbreak_consecutive_losses
            return f"⏳ Esperando {remaining} LOSS para activar"


# ==================== USER PREDICTOR ====================
class UserPredictor:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.history_window = deque(maxlen=50)
        self.pending_bet = None
        self.last_color = None
        self.active = True
        self.waiting_for_5 = True
        self.waiting_for_pattern = False
        self.rounds_to_wait = 0
        
        self.total_wins = 0
        self.total_losses = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        
        self.on_status = None
        self.on_prediction = None
        self.on_result = None
    
    def _get_historial_str(self):
        last_5 = list(self.history_window)[-5:] if len(self.history_window) >= 5 else list(self.history_window)
        history_emojis = []
        for c in last_5:
            history_emojis.append("🔴" if c == 'red' else "🔵")
        return ''.join(history_emojis) if history_emojis else "---"
    
    def _get_estado_str(self):
        if self.rounds_to_wait > 0:
            return f"Esperando despues de LOSS ({self.rounds_to_wait})"
        if self.waiting_for_5:
            return f"Esperando 5 ({len(self.history_window)}/5)"
        if self.waiting_for_pattern:
            return "Patron bloqueado"
        if self.pending_bet is not None:
            pending_emoji = "🔴" if self.pending_bet == 'red' else "🔵"
            return f"Esperando: {pending_emoji}"
        return "Listo"
    
    def _get_minority_color(self) -> str:
        last_5 = list(self.history_window)[-5:]
        blues = last_5.count('blue')
        reds = last_5.count('red')
        
        if blues < reds:
            return 'blue'
        elif reds < blues:
            return 'red'
        else:
            return last_5[-1]
    
    def _should_block(self) -> bool:
        if len(self.history_window) < 5:
            return False
        
        last_5 = list(self.history_window)[-5:]
        
        if all(c == last_5[0] for c in last_5):
            return True
        
        if last_5[0] == 'blue' and last_5.count('blue') == 1:
            return True
        
        if last_5[0] == 'red' and last_5.count('red') == 1:
            return True
        
        return False
    
    def process_color(self, color: str):
        if not self.active:
            return
        
        self.last_color = color
        self.history_window.append(color)
        
        if self.pending_bet is not None:
            self._verify_result(color)
        
        if self.rounds_to_wait > 0:
            self.rounds_to_wait -= 1
            if self.on_status:
                historial = self._get_historial_str()
                estado = self._get_estado_str()
                color_emoji = "🔴" if color == 'red' else "🔵"
                color_text = "ROJO" if color == 'red' else "AZUL"
                self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\nEstado: {estado}")
            return
        
        if self._should_block():
            self.waiting_for_pattern = True
        else:
            self.waiting_for_pattern = False
        
        if self.on_status and self.pending_bet is None:
            historial = self._get_historial_str()
            estado = self._get_estado_str()
            color_emoji = "🔴" if color == 'red' else "🔵"
            color_text = "ROJO" if color == 'red' else "AZUL"
            self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\nEstado: {estado}")
        
        if self.pending_bet is None and len(self.history_window) >= 5 and not self.waiting_for_pattern:
            self._make_prediction()
    
    def _verify_result(self, actual_color: str):
        is_win = (self.pending_bet == actual_color)
        
        if is_win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.total_wins += 1
            self.rounds_to_wait = 0
            
            if self.on_result:
                self.on_result(f"✅ WIN\nRacha: {self.consecutive_wins}", True)
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.total_losses += 1
            self.rounds_to_wait = 1
            
            if self.on_result:
                self.on_result(f"❌ LOSS\nRacha: {self.consecutive_losses}\n⏳ Esperando 1 ronda", False)
        
        self.pending_bet = None
    
    def _make_prediction(self):
        minority_color = self._get_minority_color()
        pred_emoji = "🔴" if minority_color == 'red' else "🔵"
        
        self.pending_bet = minority_color
        
        if self.on_prediction:
            self.on_prediction(f"SEÑAL {pred_emoji}")
    
    def reset(self):
        self.history_window.clear()
        self.pending_bet = None
        self.last_color = None
        self.waiting_for_5 = True
        self.waiting_for_pattern = False
        self.rounds_to_wait = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0


# ==================== POLLING GLOBAL ====================
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
    
    async def _send_message(self, user_id: int, text: str, parse_mode: str = None):
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
    
    def _sync_send_message(self, user_id: int, text: str, parse_mode: str = None):
        if self.application:
            asyncio.run_coroutine_threadsafe(self._send_message(user_id, text, parse_mode), self.loop)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        
        if not license_check['valid']:
            keyboard = [
                [InlineKeyboardButton("🎁 Probar 24h GRATIS", callback_data='plan_test_24h')],
                [InlineKeyboardButton("📊 Peak-Break 30 Dias", callback_data='plan_peakbreak_30d')],
                [InlineKeyboardButton("📊 Peak-Break Lifetime", callback_data='plan_peakbreak_lifetime')],
                [InlineKeyboardButton("💰 Ver todos los planes", callback_data='buy_license')]
            ]
            await update.message.reply_text(
                "🔒 ACCESO RESTRINGIDO\n\nNo tienes licencia activa.\n\n"
                "💰 PLANES DISPONIBLES:\n"
                "• 30 dias: 10 USDT\n"
                "• 6 meses: 35 USDT\n"
                "• 1 año: 50 USDT\n"
                "• Multiuser Lifetime: 45 USDT\n"
                "• Peak-Break 30 dias: 15 USDT\n"
                "• Peak-Break Lifetime: 60 USDT\n\n"
                "🎁 PRUEBA GRATIS: 24 horas\n\n"
                "Selecciona una opcion:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        license_data = license_check['data']
        license_type = license_data.get('type', 'single')
        plan_name = license_data.get('plan', 'unknown')
        
        keyboard = [
            [InlineKeyboardButton("📡 MODO SEÑALES", callback_data='signals_mode')],
            [InlineKeyboardButton("🤖 MODO AUTOMATICO", callback_data='auto_mode')],
            [InlineKeyboardButton("📜 Info Licencia", callback_data='license_info')],
            [InlineKeyboardButton("💰 Comprar Licencia", callback_data='buy_license')]
        ]
        
        modo_extra = ""
        if license_type == "peakbreak":
            modo_extra = "\n\n📊 MODO PEAK-BREAK ACTIVADO\n➡️ Apuesta solo despues de 3 LOSS seguidos\n➡️ Despues de cada LOSS espera 1 ronda\n➡️ Despues de un WIN, vuelve a esperar 3 LOSS"
        
        await update.message.reply_text(
            f"🎰 PREDICTOR PRO BOT\n\n"
            f"✅ Licencia: {plan_name}\n"
            f"📋 Tipo: {license_type}\n"
            f"👥 Maximo cuentas: {license_data.get('max_users', 1)}{modo_extra}\n\n"
            f"Selecciona un modo:",
            reply_markup=InlineKeyboardMarkup(keyboard)
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
            self._sync_send_message(user_id, msg)
        
        self.global_polling.register_user(user_id, on_status, on_prediction, on_result)
        self.user_sessions[user_id] = {'mode': 'signals'}
        
        await query.edit_message_text(
            "📡 MODO SEÑALES ACTIVADO\n\n"
            "Recibiras el historial, estado y señales.\n\n"
            "Usa /stop para detener."
        )
    
    async def auto_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        
        if not license_check['valid']:
            await query.edit_message_text("❌ No tienes licencia activa. Usa /start")
            return
        
        max_accounts = license_check['data'].get('max_users', 1)
        plan_type = license_check['data'].get('type', 'single')
        is_peakbreak = (plan_type == "peakbreak")
        
        mensaje = (
            f"🤖 MODO AUTOMATICO\n\n"
            f"📋 Licencia: {license_check['data']['plan']}\n"
            f"👥 Tipo: {plan_type}\n"
            f"🔢 Cuentas permitidas: {max_accounts}\n"
        )
        
        if is_peakbreak:
            mensaje += (
                f"\n📊 MODO PEAK-BREAK ACTIVO\n"
                f"➡️ Las cuentas SOLO apostaran despues de 3 LOSS seguidos\n"
                f"➡️ Despues de cada LOSS espera 1 ronda\n"
                f"➡️ Despues de un WIN, vuelven a esperar 3 LOSS\n\n"
            )
        
        if max_accounts > 1:
            mensaje += (
                f"Envia las credenciales separadas por comas:\n"
                f"usuario1:contraseña1,usuario2:contraseña2\n\n"
                f"Ejemplo:\n"
                f"juan123:abc123,maria456:xyz789"
            )
        else:
            mensaje += (
                f"Envia tus credenciales:\n"
                f"usuario:contraseña\n\n"
                f"Ejemplo:\n"
                f"juan123:abc123"
            )
        
        await query.edit_message_text(mensaje)
        context.user_data['awaiting_credentials'] = True
        context.user_data['max_accounts'] = max_accounts
        context.user_data['is_peakbreak'] = is_peakbreak
    
    async def process_credentials(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        max_accounts = context.user_data.get('max_accounts', 1)
        is_peakbreak = context.user_data.get('is_peakbreak', False)
        
        accounts_data = []
        
        if ',' in text:
            parts = text.split(',')
            for part in parts:
                part = part.strip()
                if ':' in part:
                    u, p = part.split(':', 1)
                    accounts_data.append((u.strip(), p.strip()))
        elif ':' in text:
            u, p = text.split(':', 1)
            accounts_data.append((u.strip(), p.strip()))
        else:
            await update.message.reply_text(
                "❌ Formato incorrecto.\n\n"
                "Usa:\n"
                "usuario:contraseña\n\n"
                "O para multiples cuentas:\n"
                "user1:pass1,user2:pass2"
            )
            context.user_data['awaiting_credentials'] = False
            return
        
        if len(accounts_data) > max_accounts:
            await update.message.reply_text(
                f"❌ Tu licencia permite maximo {max_accounts} cuentas.\n"
                f"Enviaste {len(accounts_data)} cuentas."
            )
            context.user_data['awaiting_credentials'] = False
            return
        
        if len(accounts_data) == 0:
            await update.message.reply_text("❌ No se detectaron credenciales validas.")
            context.user_data['awaiting_credentials'] = False
            return
        
        await update.message.reply_text(f"🔄 Probando {len(accounts_data)} cuenta(s)...")
        
        accounts = []
        for username, password in accounts_data:
            if is_peakbreak:
                acc = PeakBreakAccount(username, password)
            else:
                acc = UserAccount(username, password)
            
            success, msg = acc.login()
            if success:
                accounts.append(acc)
                await update.message.reply_text(f"✅ {username}: ${acc.balance:.2f}")
            else:
                await update.message.reply_text(f"❌ {username}: {msg}")
        
        if accounts:
            await update.message.reply_text(f"✅ {len(accounts)} cuenta(s) conectada(s) correctamente.")
            
            if not self.global_polling.running:
                self.global_polling.start()
            
            def on_status(msg):
                self._sync_send_message(user_id, msg)
            
            def on_prediction(msg):
                self._sync_send_message(user_id, msg)
                if self.user_sessions.get(user_id, {}).get('auto_betting_active'):
                    if 'SEÑAL 🔴' in msg:
                        color = 'red'
                    elif 'SEÑAL 🔵' in msg:
                        color = 'blue'
                    else:
                        color = None
                    
                    if color:
                        print(f"🎯 Auto-bet: apostando a {color}")
                        self._execute_bets(user_id, color, is_peakbreak)
            
            def on_result(msg, is_win):
                self._sync_send_message(user_id, msg)
                if self.user_sessions.get(user_id, {}).get('auto_betting_active'):
                    self._update_bet_on_result(user_id, is_win, is_peakbreak)
                    self._show_balances(user_id)
            
            self.global_polling.register_user(user_id, on_status, on_prediction, on_result)
            
            self.user_sessions[user_id] = {
                'mode': 'auto',
                'accounts': accounts,
                'auto_betting_active': False,
                'is_peakbreak': is_peakbreak,
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
        else:
            await update.message.reply_text("❌ No se pudo conectar ninguna cuenta. Verifica tus credenciales.")
        
        context.user_data['awaiting_credentials'] = False
    
    def _execute_bets(self, user_id: int, color: str, is_peakbreak: bool = False):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        
        print(f"💰 Ejecutando apuestas para usuario {user_id} - Color: {color} - PeakBreak: {is_peakbreak}")
        
        # Primero, decrementar contadores de espera de todas las cuentas PeakBreak
        if is_peakbreak:
            for account in session.get('accounts', []):
                if hasattr(account, 'decrement_wait'):
                    account.decrement_wait()
        
        for account in session.get('accounts', []):
            # Para Peak-Break, verificar si debemos apostar
            if is_peakbreak and hasattr(account, 'should_bet'):
                if not account.should_bet():
                    status = account.get_status()
                    print(f"📊 {account.username}: {status} - NO apostando")
                    continue
            
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
    
    def _update_bet_on_result(self, user_id: int, won: bool, is_peakbreak: bool = False):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        
        for account in session.get('accounts', []):
            if not account.betting_active:
                continue
            
            if is_peakbreak and hasattr(account, 'record_win') and hasattr(account, 'record_loss'):
                if won:
                    # GANO - Resetear todo
                    account.record_win()
                    account.wins += 1
                    account.reset_bet()
                    self._sync_send_message(user_id, f"💰 {account.username}: WIN - Apuesta reiniciada a ${account.current_bet:.2f}")
                else:
                    # PERDIO - Registrar la perdida (esto activa wait_rounds=1)
                    was_active = account.record_loss()
                    
                    if was_active:
                        # Estaba activo, actualizar apuesta con Martingala
                        account.losses += 1
                        if account.consecutive_losses + 1 >= account.max_consecutive_losses:
                            self._sync_send_message(user_id, f"🛑 {account.username}: Stop loss alcanzado")
                            account.betting_active = False
                        else:
                            msg = account.update_bet_on_loss()
                            self._sync_send_message(user_id, f"📉 {account.username}: {msg}")
                    else:
                        # No estaba activo, solo mostrar progreso
                        remaining = account.PEAKBREAK_THRESHOLD - account.peakbreak_consecutive_losses
                        self._sync_send_message(user_id, f"📊 {account.username}: Perdida registrada ({account.peakbreak_consecutive_losses}/{account.PEAKBREAK_THRESHOLD}) - Faltan {remaining} LOSS para activar - Esperando 1 ronda")
            else:
                # Modo normal (no Peak-Break)
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
        is_peakbreak = session.get('is_peakbreak', False)
        
        msg = "💰 SALDOS ACTUALIZADOS\n\n"
        for acc in session.get('accounts', []):
            acc.get_balance()
            msg += f"• {acc.username}: ${acc.balance:.2f}\n"
            if is_peakbreak and hasattr(acc, 'get_status'):
                msg += f"  └─ {acc.get_status()}\n"
        
        self._sync_send_message(user_id, msg)
    
    async def show_betting_config(self, update, user_id):
        session = self.user_sessions[user_id]
        config = session['bet_config']
        is_peakbreak = session.get('is_peakbreak', False)
        
        keyboard = [
            [InlineKeyboardButton(f"💰 Inicial: ${config['initial_bet']}", callback_data='cfg_initial')],
            [InlineKeyboardButton(f"📈 Maximo: ${config['max_bet']}", callback_data='cfg_max_bet')],
            [InlineKeyboardButton(f"🛑 Max Losses: {config['max_losses']}", callback_data='cfg_max_losses')],
            [InlineKeyboardButton(f"🎲 Martingala: {'✅' if config['use_martingale'] else '❌'}", callback_data='cfg_martingale')],
            [InlineKeyboardButton(f"⚡ Agresiva: {'✅' if config['use_aggressive'] else '❌'}", callback_data='cfg_aggressive')],
            [InlineKeyboardButton("📊 Ver Balances", callback_data='view_balances')],
            [InlineKeyboardButton("▶️ INICIAR AUTO-BET", callback_data='start_autobet')],
            [InlineKeyboardButton("◀️ Volver", callback_data='back_to_start')]
        ]
        
        modo_texto = ""
        if is_peakbreak:
            modo_texto = "\n\n📊 PEAK-BREAK MODE\n➡️ Apuesta solo despues de 3 LOSS seguidos\n➡️ Despues de cada LOSS espera 1 ronda"
        
        msg = (
            f"⚙️ CONFIGURACION\n\n"
            f"💰 Apuesta actual: ${config['current_bet']}\n"
            f"🎲 Martingala: {'✅' if config['use_martingale'] else '❌'}\n"
            f"⚡ Agresiva: {'✅' if config['use_aggressive'] else '❌'}{modo_texto}"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def cfg_initial(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("💰 Envia nuevo monto inicial (minimo 0.1):\nEj: 0.5")
        context.user_data['awaiting_initial_bet'] = True
    
    async def cfg_max_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("📈 Envia monto maximo:\nEj: 10.0")
        context.user_data['awaiting_max_bet'] = True
    
    async def cfg_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🛑 Envia numero maximo de perdidas:\nEj: 5")
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
                await update.message.reply_text("❌ Minimo 0.1")
                return
            self.user_sessions[user_id]['bet_config']['initial_bet'] = amount
            self.user_sessions[user_id]['bet_config']['current_bet'] = amount
            for acc in self.user_sessions[user_id]['accounts']:
                acc.initial_bet = amount
                acc.current_bet = amount
            await update.message.reply_text(f"✅ Monto inicial: ${amount:.2f}")
            await self.show_betting_config(update, user_id)
        except:
            await update.message.reply_text("❌ Numero invalido")
        context.user_data['awaiting_initial_bet'] = False
    
    async def process_max_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        try:
            amount = float(update.message.text)
            if amount < 0.1:
                await update.message.reply_text("❌ Minimo 0.1")
                return
            self.user_sessions[user_id]['bet_config']['max_bet'] = amount
            for acc in self.user_sessions[user_id]['accounts']:
                acc.max_bet = amount
            await update.message.reply_text(f"✅ Maximo: ${amount:.2f}")
            await self.show_betting_config(update, user_id)
        except:
            await update.message.reply_text("❌ Numero invalido")
        context.user_data['awaiting_max_bet'] = False
    
    async def process_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        try:
            value = int(update.message.text)
            if value < 1:
                await update.message.reply_text("❌ Minimo 1")
                return
            if value > 20:
                await update.message.reply_text("❌ Maximo 20")
                return
            self.user_sessions[user_id]['bet_config']['max_losses'] = value
            for acc in self.user_sessions[user_id]['accounts']:
                acc.max_consecutive_losses = value
            await update.message.reply_text(f"✅ Max Losses: {value}")
            await self.show_betting_config(update, user_id)
        except:
            await update.message.reply_text("❌ Numero invalido")
        context.user_data['awaiting_max_losses'] = False
    
    async def start_autobet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_sessions:
            await update.callback_query.answer("❌ No hay cuentas")
            return
        
        config = self.user_sessions[user_id]['bet_config']
        is_peakbreak = self.user_sessions[user_id].get('is_peakbreak', False)
        
        for acc in self.user_sessions[user_id]['accounts']:
            acc.initial_bet = config['initial_bet']
            acc.current_bet = config['initial_bet']
            acc.max_bet = config['max_bet']
            acc.max_consecutive_losses = config['max_losses']
            acc.use_martingale = config['use_martingale']
            acc.use_aggressive = config['use_aggressive']
            acc.consecutive_losses = 0
            acc.betting_active = True
            
            if is_peakbreak and hasattr(acc, 'reset_peakbreak'):
                acc.reset_peakbreak()
        
        self.user_sessions[user_id]['auto_betting_active'] = True
        
        modo_texto = ""
        if is_peakbreak:
            modo_texto = (
                f"\n\n📊 PEAK-BREAK MODE\n"
                f"➡️ Apuesta solo despues de 3 LOSS seguidos\n"
                f"➡️ Despues de cada LOSS espera 1 ronda\n"
                f"➡️ Despues de WIN → espera 3 LOSS"
            )
        
        await update.callback_query.edit_message_text(
            f"✅ AUTO-BET ACTIVADO\n\n"
            f"💰 Inicial: ${config['initial_bet']}\n"
            f"📈 Maximo: ${config['max_bet']}\n"
            f"🛑 Max Losses: {config['max_losses']}\n"
            f"🎲 Martingala: {'✅' if config['use_martingale'] else '❌'}\n"
            f"⚡ Agresiva: {'✅' if config['use_aggressive'] else '❌'}{modo_texto}\n\n"
            f"📊 Cuentas activas: {len(self.user_sessions[user_id]['accounts'])}\n\n"
            f"Usa /stop para detener."
        )
    
    async def view_balances(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        session = self.user_sessions.get(user_id)
        if not session:
            await update.callback_query.answer("No hay sesion")
            return
        
        is_peakbreak = session.get('is_peakbreak', False)
        msg = "💰 BALANCES\n\n"
        for acc in session.get('accounts', []):
            acc.get_balance()
            msg += f"• {acc.username}: ${acc.balance:.2f}\n"
            if is_peakbreak and hasattr(acc, 'get_status'):
                msg += f"  └─ {acc.get_status()}\n"
        
        await update.callback_query.edit_message_text(msg)
    
    async def buy_license(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🎁 PRUEBA 24h (GRATIS)", callback_data='plan_test_24h')],
            [InlineKeyboardButton("📅 30 Dias - 10 USDT", callback_data='plan_30d')],
            [InlineKeyboardButton("📅 6 Meses - 35 USDT", callback_data='plan_6m')],
            [InlineKeyboardButton("📅 1 Año - 50 USDT", callback_data='plan_1y')],
            [InlineKeyboardButton("👥 Multiuser Lifetime - 45 USDT", callback_data='plan_lifetime_multiuser')],
            [InlineKeyboardButton("📊 Peak-Break 30 Dias - 15 USDT", callback_data='plan_peakbreak_30d')],
            [InlineKeyboardButton("📊 Peak-Break Lifetime - 60 USDT", callback_data='plan_peakbreak_lifetime')]
        ]
        await update.callback_query.edit_message_text(
            "💰 COMPRAR LICENCIA\n\nSelecciona un plan:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def select_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        plan_id = query.data.replace('plan_', '')
        user_id = update.effective_user.id
        
        if plan_id == "test_24h":
            existing = self.license_manager.check_license(user_id)
            if existing['valid']:
                await query.edit_message_text("❌ Ya tienes una licencia activa. No puedes activar la prueba.")
                return
            
            if self.license_manager.activate_license(user_id, "test_24h"):
                await query.edit_message_text(
                    "🎁 LICENCIA DE PRUEBA ACTIVADA\n\n"
                    "✅ Duracion: 24 horas\n"
                    "✅ Acceso completo a todas las funciones\n\n"
                    "Usa /start para comenzar.\n\n"
                    "⏳ La licencia expirara en 24 horas."
                )
            else:
                await query.edit_message_text("❌ Error al activar la licencia de prueba.")
            return
        
        plan = LICENSE_PLANS.get(plan_id)
        if not plan:
            await query.edit_message_text("❌ Plan invalido")
            return
        
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
            f"💸 PAGO REQUERIDO\n\n"
            f"📦 Plan: {plan['name']}\n"
            f"💰 Monto: {plan['price']} USDT\n"
            f"🔗 Red: BEP20\n\n"
            f"📤 Wallet:\n{MY_WALLET_BEP20}\n\n"
            f"1️⃣ Transferir EXACTAMENTE {plan['price']} USDT (BEP20)\n"
            f"2️⃣ Toca 📸 Enviar Comprobante\n"
            f"3️⃣ Adjunta CAPTURA con TXID\n\n"
            f"🆔 Tu ID: {user_id}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def send_payment_proof(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        
        if user_id not in self.pending_payments:
            await query.edit_message_text("❌ No hay compra pendiente.")
            return
        
        await query.edit_message_text(
            "📸 ENVIA CAPTURA\n\n"
            "Envia la imagen con el TXID visible.\n\n"
            "Ejemplo de TXID: 0x... o codigo de transaccion."
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
            f"🆕 NUEVO PAGO\n\n"
            f"👤 @{username}\n"
            f"🆔 {user_id}\n"
            f"📦 {plan_name}\n"
            f"💰 {amount} USDT\n"
            f"📝 {txid}\n\n"
            f"✅ /validar {user_id} {plan_info['plan']}"
        )
        
        try:
            if update.message.photo:
                photo = update.message.photo[-1]
                await self.application.bot.send_photo(
                    chat_id=ADMIN_GROUP_ID,
                    photo=photo.file_id,
                    caption=admin_msg
                )
            elif update.message.document:
                doc = update.message.document
                await self.application.bot.send_document(
                    chat_id=ADMIN_GROUP_ID,
                    document=doc.file_id,
                    caption=admin_msg
                )
            else:
                await update.message.reply_text("❌ Envia una imagen o documento con el comprobante.")
                return
            
            await update.message.reply_text("✅ Comprobante enviado. Sera verificado por un administrador.")
            del self.pending_payments[user_id]
            context.user_data['awaiting_payment_proof'] = False
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    async def license_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        
        if license_check['valid']:
            data = license_check['data']
            expiry = datetime.fromisoformat(data['expiry'])
            days = (expiry - datetime.now()).days
            extra = ""
            
            if data.get('type') == "peakbreak":
                extra = (
                    "\n\n📊 PEAK-BREAK MODE\n"
                    "➡️ Apuesta solo despues de 3 LOSS seguidos\n"
                    "➡️ Despues de cada LOSS espera 1 ronda\n"
                    "➡️ Despues de WIN → espera 3 LOSS"
                )
            
            await query.edit_message_text(
                f"📜 INFORMACION DE LICENCIA\n\n"
                f"📋 Plan: {data['plan']}\n"
                f"👥 Tipo: {data['type']}\n"
                f"🔢 Maximo cuentas: {data.get('max_users', 1)}\n"
                f"📅 Activada: {datetime.fromisoformat(data['activated']).strftime('%Y-%m-%d')}\n"
                f"⏰ Expira: {expiry.strftime('%Y-%m-%d')}\n"
                f"📆 Dias restantes: {days if days < 3650 else '∞'}{extra}"
            )
        else:
            await query.edit_message_text(
                "❌ Sin licencia activa\n\n"
                "Usa /start para ver los planes disponibles."
            )
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_sessions:
            self.user_sessions[user_id]['auto_betting_active'] = False
            self.global_polling.unregister_user(user_id)
            del self.user_sessions[user_id]
        await update.message.reply_text(
            "⏹️ Auto-bet detenido\n\n"
            "Usa /start para volver a iniciar."
        )
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("❌ Operacion cancelada.")
    
    async def validate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ No autorizado")
            return
        
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "📋 USO: /validar USER_ID PLAN\n\n"
                "PLANES DISPONIBLES:\n"
                "• test_24h - Prueba 24h\n"
                "• 30d - 30 dias\n"
                "• 6m - 6 meses\n"
                "• 1y - 1 año\n"
                "• lifetime_multiuser - Multiuser\n"
                "• peakbreak_30d - Peak-Break 30 dias\n"
                "• peakbreak_lifetime - Peak-Break Lifetime"
            )
            return
        
        user_id = int(args[0])
        plan = args[1]
        
        if self.license_manager.activate_license(user_id, plan):
            await update.message.reply_text(f"✅ Licencia {plan} activada para usuario {user_id}")
            await self._send_message(
                user_id, 
                f"🎉 LICENCIA ACTIVADA!\n\n"
                f"📦 Plan: {plan}\n"
                f"✅ Ya puedes usar el bot.\n\n"
                f"Usa /start para comenzar."
            )
        else:
            await update.message.reply_text("❌ Plan invalido. Verifica la lista de planes.")
    
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
                await update.message.reply_text("❌ Envia una imagen o documento con el comprobante.")
    
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
        print("📊 Estrategia: Minoria + Patrones bloqueados")
        print("📊 Patrones que NO apuestan: 5 iguales, 🔵🔴🔴🔴🔴, 🔴🔵🔵🔵🔵")
        print("⏳ Espera: 1 ronda despues de cada LOSS")
        print("👥 Multiuser: Hasta 5 cuentas por usuario")
        print("📊 Peak-Break: Apuesta solo despues de 3 LOSS seguidos")
        print("📊 Peak-Break: Despues de CADA LOSS espera 1 ronda")  # ← NUEVO
        print("💰 Precios: Peak-Break Lifetime = 60 USDT")
        print("🎁 Licencia de prueba: 24 horas")
        print("=" * 50)
        
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    print("🚀 INICIANDO PREDICTOR PRO BOT...")
    bot = PredictionBot(BOT_TOKEN)
    bot.run()
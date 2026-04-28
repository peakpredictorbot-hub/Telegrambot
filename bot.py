# bot.py - PREDICTOR PRO BOT (3 MODOS: ESTÁNDAR, PEAK-BREAK, PEAK-GHOST)
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
ADMIN_IDS = [8051843698]
ADMIN_GROUP_ID = -1003731875494
MY_WALLET_BEP20 = "0x621917958C7ac81190e9f876C23D6B9914f31263"

# ==================== PLANES DE LICENCIA ====================
LICENSE_PLANS = {
    "test_24h": {"price": 0, "days": 1, "mode": "standard", "max_users": 1, "name": "🎁 Prueba 24h (GRATIS)"},
    "standard": {"price": 10, "days": 30, "mode": "standard", "max_users": 1, "name": "📅 Estándar 30 Días"},
    "peakbreak": {"price": 15, "days": 30, "mode": "peakbreak", "max_users": 1, "name": "📊 Peak-Break 30 Días"},
    "ghost": {"price": 20, "days": 30, "mode": "ghost", "max_users": 1, "name": "👻 Peak-Ghost 30 Días"},
    "multiuser": {"price": 45, "days": 30, "mode": "flexible", "max_users": 5, "name": "👥 Multiuser 30 Días"},
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
            "expiry": expiry_date.isoformat(), "mode": plan_config["mode"],
            "max_users": plan_config["max_users"], "active": True
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
    
    def get_remaining_days(self, user_id: int) -> int:
        license_data = self.licenses.get(str(user_id))
        if not license_data:
            return 0
        expiry = datetime.fromisoformat(license_data["expiry"])
        days = (expiry - datetime.now()).days
        return max(0, days)

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
        self.use_martingale = False  # False = modo agresivo (x2+1)
        
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
            return f"Martingale (x2): ${new_bet:.2f}"
        else:
            # Modo agresivo: (actual x 2) + 1
            new_bet = min((self.current_bet * 2) + 1, self.max_bet)
            self.current_bet = new_bet
            return f"Aggressive (x2+1): ${new_bet:.2f}"

# ==================== ESTRATEGIA BASE (ESTÁNDAR) ====================
class StandardStrategy:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.history_window = deque(maxlen=20)
        self.pending_bet = None
        self.last_color = None
        self.active = True
        self.rounds_to_wait = 0
        
        self.alternating_mode = False
        self.consecutive_alternations = 0
        
        self.total_wins = 0
        self.total_losses = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        
        self.on_status = None
        self.on_prediction = None
        self.on_result = None
    
    def _calculate_wait_rounds(self) -> int:
        if self.consecutive_losses <= 0:
            return 0
        elif self.consecutive_losses % 2 == 1:
            return 1
        else:
            return 2
    
    def _detect_alternating_pattern(self) -> bool:
        if len(self.history_window) < 3:
            return False
        last_3 = list(self.history_window)[-3:]
        if last_3[0] != last_3[1] and last_3[1] != last_3[2]:
            return True
        if len(self.history_window) >= 4:
            last_4 = list(self.history_window)[-4:]
            if last_4[0] != last_4[1] and last_4[1] != last_4[2] and last_4[2] != last_4[3]:
                return True
        return False
    
    def _should_follow_alternating(self) -> bool:
        if len(self.history_window) < 2:
            return False
        if self.alternating_mode:
            last_2 = list(self.history_window)[-2:]
            if len(last_2) == 2 and last_2[0] == last_2[1]:
                self.alternating_mode = False
                self.consecutive_alternations = 0
                return False
            return True
        if self._detect_alternating_pattern():
            self.alternating_mode = True
            return True
        return False
    
    def _get_prediction(self) -> Optional[str]:
        if len(self.history_window) == 0:
            return None
        current_color = list(self.history_window)[-1]
        if self._should_follow_alternating():
            prediction = 'blue' if current_color == 'red' else 'red'
            self.consecutive_alternations += 1
            return prediction
        return current_color
    
    def _get_historial_str(self):
        last_10 = list(self.history_window)[-10:] if len(self.history_window) >= 10 else list(self.history_window)
        history_emojis = ["🔴" if c == 'red' else "🔵" for c in last_10]
        return ''.join(history_emojis) if history_emojis else "---"
    
    def _get_estado_str(self):
        if self.rounds_to_wait > 0:
            return f"⏳ Esperando {self.rounds_to_wait} ronda(s) después de LOSS #{self.consecutive_losses}"
        if self.alternating_mode:
            return f"🔄 Siguiendo alternancia"
        return "📊 Siguiendo último color"
    
    def _update_status_display(self, current_color: str):
        color_emoji = "🔴" if current_color == 'red' else "🔵"
        color_text = "ROJO" if current_color == 'red' else "AZUL"
        historial = self._get_historial_str()
        estado = self._get_estado_str()
        
        if self.on_status:
            self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\n{estado}")
    
    def _make_prediction(self):
        if self.rounds_to_wait > 0:
            return
        prediction = self._get_prediction()
        if prediction is None:
            return
        self.pending_bet = prediction
        pred_emoji = "🔴" if prediction == 'red' else "🔵"
        pred_text = "ROJO" if prediction == 'red' else "AZUL"
        if self.on_prediction:
            self.on_prediction(f"🎯 SEÑAL: {pred_emoji} {pred_text}")
    
    def _verify_result(self, is_win: bool, result_color: str):
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
            self.rounds_to_wait = self._calculate_wait_rounds()
            if self.on_result:
                self.on_result(f"❌ LOSS #{self.consecutive_losses}\n⏳ Esperando {self.rounds_to_wait} ronda(s)", False)
        self.pending_bet = None
    
    def process_color(self, color: str):
        if not self.active:
            return
        
        # Verificar resultado de apuesta pendiente
        if self.pending_bet is not None:
            is_win = (self.pending_bet == color)
            self._verify_result(is_win, color)
            # Si fue WIN, agregar color y generar nueva predicción
            if is_win:
                self.history_window.append(color)
                self._update_status_display(color)
                self._make_prediction()
                return
            # Si fue LOSS, no agregar color aún (se agregará abajo)
        
        # Agregar color al historial
        self.history_window.append(color)
        
        # Manejar espera post-LOSS
        if self.rounds_to_wait > 0:
            self.rounds_to_wait -= 1
            self._update_status_display(color)
            if self.rounds_to_wait == 0:
                self._make_prediction()
            return
        
        # Generar nueva predicción si no hay pendiente
        if self.pending_bet is None:
            self._make_prediction()
        
        # Actualizar display de estado
        self._update_status_display(color)
    
    def reset(self):
        self.history_window.clear()
        self.pending_bet = None
        self.last_color = None
        self.rounds_to_wait = 0
        self.alternating_mode = False
        self.consecutive_alternations = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0

# ==================== ESTRATEGIA PEAK-BREAK ====================
class PeakBreakStrategy(StandardStrategy):
    """
    PEAK-BREAK: Solo apuesta después de 2 LOSS consecutivos en el juego
    """
    def __init__(self, user_id: int):
        super().__init__(user_id)
        self.peak_active = False
        self.loss_streak = 0
    
    def _update_status_display(self, current_color: str):
        color_emoji = "🔴" if current_color == 'red' else "🔵"
        color_text = "ROJO" if current_color == 'red' else "AZUL"
        historial = self._get_historial_str()
        
        if self.peak_active:
            estado = "⚡ ACTIVO (apostando)"
            if self.rounds_to_wait > 0:
                estado = f"⚡ ACTIVO - Esperando {self.rounds_to_wait} ronda(s) después de LOSS #{self.consecutive_losses}"
        else:
            remaining = 2 - self.loss_streak
            if remaining <= 0:
                remaining = 0
            estado = f"⏳ Peak-Break: esperando {remaining} LOSS para activar"
        
        if self.on_status:
            self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\n{estado}")
    
    def process_color(self, color: str):
        if not self.active:
            return
        
        # Verificar resultado de apuesta pendiente (si está activo)
        if self.peak_active and self.pending_bet is not None:
            is_win = (self.pending_bet == color)
            self._verify_result(is_win, color)
            if is_win:
                # WIN desactiva el modo Peak-Break
                self.peak_active = False
                self.loss_streak = 0
                self.history_window.append(color)
                self._update_status_display(color)
                if self.on_status:
                    self.on_status(f"🔴 PEAK-BREAK DESACTIVADO (WIN obtenido)")
                return
            else:
                # LOSS: agregar color y continuar
                self.history_window.append(color)
                self._update_status_display(color)
                return
        
        # Agregar color al historial SIEMPRE
        self.history_window.append(color)
        
        # Contar LOSS consecutivos en el juego (para activación)
        if len(self.history_window) >= 2:
            last_two = list(self.history_window)[-2:]
            if last_two[0] == last_two[1]:
                self.loss_streak = 2
            else:
                self.loss_streak = 1
        else:
            self.loss_streak = 1
        
        # Verificar activación por 2 LOSS consecutivos
        if not self.peak_active and self.loss_streak >= 2:
            self.peak_active = True
            self.rounds_to_wait = 0
            if self.on_status:
                self.on_status(f"⚡ PEAK-BREAK ACTIVADO (2 LOSS seguidos)")
        
        # Si no está activo, solo mostrar estado y salir
        if not self.peak_active:
            self._update_status_display(color)
            return
        
        # Si está activo, manejar espera post-LOSS
        if self.rounds_to_wait > 0:
            self.rounds_to_wait -= 1
            self._update_status_display(color)
            if self.rounds_to_wait == 0:
                self._make_prediction()
            return
        
        # Generar nueva predicción si no hay pendiente
        if self.pending_bet is None:
            self._make_prediction()
        
        # Actualizar display
        self._update_status_display(color)

# ==================== ESTRATEGIA PEAK-GHOST ====================
class PeakGhostStrategy(StandardStrategy):
    """
    PEAK-GHOST: WIN→LOSS activa espera hasta ver WIN+LOSS en juego
    """
    def __init__(self, user_id: int):
        super().__init__(user_id)
        self.ghost_mode = False
        self.last_was_win = False
    
    def _update_status_display(self, current_color: str):
        color_emoji = "🔴" if current_color == 'red' else "🔵"
        color_text = "ROJO" if current_color == 'red' else "AZUL"
        historial = self._get_historial_str()
        
        if self.ghost_mode:
            estado = "👻 GHOST MODE - Esperando patrón WIN+LOSS en el juego"
        elif self.rounds_to_wait > 0:
            estado = f"⏳ Esperando {self.rounds_to_wait} ronda(s) después de LOSS #{self.consecutive_losses}"
        elif self.alternating_mode:
            estado = f"🔄 Siguiendo alternancia"
        else:
            estado = "📊 Siguiendo último color"
        
        if self.on_status:
            self.on_status(f"{color_emoji} {color_text}\nHistorial: {historial}\n{estado}")
    
    def _check_ghost_pattern(self) -> bool:
        """Busca patrón WIN+LOSS en el historial (alternancia)"""
        if len(self.history_window) >= 2:
            last_two = list(self.history_window)[-2:]
            # Si son diferentes, es WIN+LOSS
            if last_two[0] != last_two[1]:
                return True
        return False
    
    def process_color(self, color: str):
        if not self.active:
            return
        
        # Verificar resultado de apuesta pendiente (modo normal)
        if not self.ghost_mode and self.pending_bet is not None:
            is_win = (self.pending_bet == color)
            self._verify_result(is_win, color)
            
            # Guardar si fue WIN para posible activación de ghost
            self.last_was_win = is_win
            
            if is_win:
                self.history_window.append(color)
                self._update_status_display(color)
                self._make_prediction()
                return
            else:
                # Si fue LOSS y veníamos de un WIN, activar GHOST MODE
                if self.last_was_win:
                    self.ghost_mode = True
                    self.pending_bet = None
                    self.history_window.append(color)
                    self._update_status_display(color)
                    if self.on_status:
                        self.on_status(f"👻 PEAK-GHOST ACTIVADO: Esperando patrón WIN+LOSS")
                    return
                else:
                    self.history_window.append(color)
                    self._update_status_display(color)
                    return
        
        # Agregar color al historial
        self.history_window.append(color)
        
        # Verificar si estamos en GHOST MODE
        if self.ghost_mode:
            # Buscar patrón WIN+LOSS en el historial
            if self._check_ghost_pattern():
                self.ghost_mode = False
                self.last_was_win = False
                if self.on_status:
                    self.on_status(f"👻 Patrón WIN+LOSS detectado! Reactivando...")
                # Una vez reactivado, procesar normalmente
            else:
                self._update_status_display(color)
                return
        
        # Modo normal: manejar espera post-LOSS
        if self.rounds_to_wait > 0:
            self.rounds_to_wait -= 1
            self._update_status_display(color)
            if self.rounds_to_wait == 0:
                self._make_prediction()
            return
        
        # Generar nueva predicción si no hay pendiente
        if self.pending_bet is None:
            self._make_prediction()
        
        # Actualizar display
        self._update_status_display(color)

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
        self.user_strategies: Dict[int, object] = {}
        self.user_strategy_type: Dict[int, str] = {}
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
    
    def register_user(self, user_id: int, strategy_type: str, on_status=None, on_prediction=None, on_result=None):
        with self._lock:
            if strategy_type == "standard":
                strategy = StandardStrategy(user_id)
            elif strategy_type == "peakbreak":
                strategy = PeakBreakStrategy(user_id)
            elif strategy_type == "ghost":
                strategy = PeakGhostStrategy(user_id)
            else:
                strategy = StandardStrategy(user_id)
            
            strategy.on_status = on_status
            strategy.on_prediction = on_prediction
            strategy.on_result = on_result
            self.user_strategies[user_id] = strategy
            self.user_strategy_type[user_id] = strategy_type
            return strategy
    
    def unregister_user(self, user_id: int):
        with self._lock:
            if user_id in self.user_strategies:
                self.user_strategies[user_id].active = False
                del self.user_strategies[user_id]
                if user_id in self.user_strategy_type:
                    del self.user_strategy_type[user_id]
    
    def start(self):
        if self.running:
            return
        self.running = True
        self.last_color_time = time.time()
        threading.Thread(target=self._polling_loop, daemon=True).start()
    
    def stop(self):
        self.running = False
    
    def _polling_loop(self):
        while self.running:
            try:
                if time.time() - self.last_color_time > self.reconnect_timeout:
                    self.last_processed_index = 0
                    self.last_color_time = time.time()
                
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
                            with self._lock:
                                for user_id, strategy in self.user_strategies.items():
                                    if strategy.active:
                                        strategy.process_color(last_color)
            except Exception as e:
                print(f"Error en polling: {e}")
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
                    chat_id=user_id, text=text, parse_mode=parse_mode,
                    read_timeout=15, write_timeout=15, connect_timeout=15
                )
        except Exception as e:
            print(f"Error enviando mensaje: {e}")
    
    def _sync_send_message(self, user_id: int, text: str, parse_mode: str = None):
        if self.application:
            asyncio.run_coroutine_threadsafe(self._send_message(user_id, text, parse_mode), self.loop)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        
        if not license_check['valid']:
            keyboard = [
                [InlineKeyboardButton("🎁 Probar 24h GRATIS", callback_data='plan_test_24h')],
                [InlineKeyboardButton("📅 Estándar 30d - 10 USDT", callback_data='plan_standard')],
                [InlineKeyboardButton("📊 Peak-Break 30d - 15 USDT", callback_data='plan_peakbreak')],
                [InlineKeyboardButton("👻 Peak-Ghost 30d - 20 USDT", callback_data='plan_ghost')],
                [InlineKeyboardButton("👥 Multiuser 30d - 45 USDT", callback_data='plan_multiuser')]
            ]
            await update.message.reply_text(
                "🔒 ACCESO RESTRINGIDO\n\nNo tienes licencia activa.\n\n"
                "💰 PLANES DISPONIBLES:\n"
                "• Prueba 24h: 0 USDT (solo Estándar)\n"
                "• Estándar 30d: 10 USDT\n"
                "• Peak-Break 30d: 15 USDT\n"
                "• Peak-Ghost 30d: 20 USDT\n"
                "• Multiuser 30d: 45 USDT (hasta 5 cuentas, elige modo)\n\n"
                "Selecciona una opción:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        license_data = license_check['data']
        plan_name = LICENSE_PLANS[license_data['plan']]['name']
        max_accounts = license_data.get('max_users', 1)
        allowed_mode = license_data.get('mode', 'standard')
        
        keyboard = [
            [InlineKeyboardButton("📡 MODO SEÑALES", callback_data='signals_mode')],
            [InlineKeyboardButton("🤖 MODO AUTOMATICO", callback_data='auto_mode')],
            [InlineKeyboardButton("📜 Info Licencia", callback_data='license_info')],
            [InlineKeyboardButton("💰 Comprar Licencia", callback_data='buy_license')]
        ]
        
        mode_msg = ""
        if allowed_mode == "flexible":
            mode_msg = "\n\n✅ Licencia MULTIUSUARIO - Puedes usar cualquier modo"
        elif allowed_mode == "standard":
            mode_msg = "\n\n📌 Tu licencia solo permite modo ESTÁNDAR"
        elif allowed_mode == "peakbreak":
            mode_msg = "\n\n📌 Tu licencia solo permite modo PEAK-BREAK"
        elif allowed_mode == "ghost":
            mode_msg = "\n\n📌 Tu licencia solo permite modo PEAK-GHOST"
        
        await update.message.reply_text(
            f"🎰 PREDICTOR PRO BOT\n\n"
            f"✅ Licencia: {plan_name}\n"
            f"👥 Máx cuentas: {max_accounts}{mode_msg}\n\n"
            f"📊 ESTRATEGIAS DISPONIBLES:\n"
            f"• ESTÁNDAR: Apostar al último color, espera 1,2,1,2...\n"
            f"• PEAK-BREAK: Entrar después de 2 LOSS seguidos\n"
            f"• PEAK-GHOST: WIN→LOSS activa espera hasta ver WIN+LOSS\n\n"
            f"Selecciona una opción:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def signals_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        
        license_check = self.license_manager.check_license(user_id)
        if not license_check['valid']:
            await query.edit_message_text("❌ Licencia no válida. Usa /start")
            return
        
        license_data = license_check['data']
        allowed_mode = license_data.get('mode', 'standard')
        
        if not self.global_polling.running:
            self.global_polling.start()
        
        def on_status(msg):
            self._sync_send_message(user_id, msg)
        
        def on_prediction(msg):
            self._sync_send_message(user_id, msg)
        
        def on_result(msg, is_win):
            self._sync_send_message(user_id, msg)
        
        # Determinar estrategia a usar
        if allowed_mode == "flexible":
            # Multiuser: por defecto usa estándar, pero puede cambiar
            strategy_type = "standard"
        else:
            strategy_type = allowed_mode
        
        self.global_polling.register_user(user_id, strategy_type, on_status, on_prediction, on_result)
        self.user_sessions[user_id] = {'mode': 'signals', 'strategy': strategy_type}
        
        await query.edit_message_text(
            f"📡 MODO SEÑALES ACTIVADO\n\n"
            f"📊 Estrategia: {strategy_type.upper()}\n\n"
            f"Recibirás las señales automáticamente.\n\n"
            f"Usa /stop para detener."
        )
    
    async def auto_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        license_check = self.license_manager.check_license(user_id)
        
        if not license_check['valid']:
            await query.edit_message_text("❌ No tienes licencia activa. Usa /start")
            return
        
        license_data = license_check['data']
        max_accounts = license_data.get('max_users', 1)
        allowed_mode = license_data.get('mode', 'standard')
        
        mensaje = (
            f"🤖 MODO AUTOMATICO\n\n"
            f"📋 Licencia: {LICENSE_PLANS[license_data['plan']]['name']}\n"
            f"🔢 Cuentas permitidas: {max_accounts}\n"
        )
        
        if allowed_mode == "flexible":
            mensaje += (
                f"\n📊 SELECCIONA ESTRATEGIA:\n"
                f"1️⃣ Estándar\n"
                f"2️⃣ Peak-Break\n"
                f"3️⃣ Peak-Ghost\n\n"
                f"Envía el número de la estrategia que quieres usar:\n"
                f"Ej: /estrategia 2"
            )
            context.user_data['awaiting_strategy_selection'] = True
            context.user_data['max_accounts'] = max_accounts
            await query.edit_message_text(mensaje)
        else:
            mensaje += (
                f"\n📊 Estrategia asignada: {allowed_mode.upper()}\n\n"
                f"Envía tus credenciales:\n"
                f"usuario:contraseña\n\n"
                f"Para múltiples cuentas (máx {max_accounts}):\n"
                f"user1:pass1,user2:pass2"
            )
            context.user_data['awaiting_credentials'] = True
            context.user_data['max_accounts'] = max_accounts
            context.user_data['forced_strategy'] = allowed_mode
            await query.edit_message_text(mensaje)
    
    async def select_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        strategy_map = {
            "1": "standard", "estándar": "standard", "standard": "standard",
            "2": "peakbreak", "peak-break": "peakbreak", "peakbreak": "peakbreak",
            "3": "ghost", "peak-ghost": "ghost", "peakghost": "ghost"
        }
        
        strategy = strategy_map.get(text.lower())
        if not strategy:
            await update.message.reply_text("❌ Opción inválida. Envía 1, 2 o 3")
            return
        
        context.user_data['selected_strategy'] = strategy
        context.user_data['awaiting_strategy_selection'] = False
        context.user_data['awaiting_credentials'] = True
        
        await update.message.reply_text(
            f"✅ Estrategia seleccionada: {strategy.upper()}\n\n"
            f"Envía tus credenciales:\n"
            f"usuario:contraseña\n\n"
            f"Para múltiples cuentas (máx {context.user_data.get('max_accounts', 1)}):\n"
            f"user1:pass1,user2:pass2"
        )
    
    async def process_credentials(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        max_accounts = context.user_data.get('max_accounts', 1)
        strategy = context.user_data.get('selected_strategy', context.user_data.get('forced_strategy', 'standard'))
        
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
            await update.message.reply_text("❌ Formato incorrecto. Usa usuario:contraseña")
            context.user_data['awaiting_credentials'] = False
            return
        
        if len(accounts_data) > max_accounts:
            await update.message.reply_text(f"❌ Tu licencia permite máximo {max_accounts} cuentas.")
            context.user_data['awaiting_credentials'] = False
            return
        
        await update.message.reply_text(f"🔄 Probando {len(accounts_data)} cuenta(s)...")
        
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
            await update.message.reply_text(f"✅ {len(accounts)} cuenta(s) conectada(s).")
            
            if not self.global_polling.running:
                self.global_polling.start()
            
            def on_status(msg):
                self._sync_send_message(user_id, msg)
            
            def on_prediction(msg):
                self._sync_send_message(user_id, msg)
                if self.user_sessions.get(user_id, {}).get('auto_betting_active'):
                    color = None
                    if '🔴' in msg:
                        color = 'red'
                    elif '🔵' in msg:
                        color = 'blue'
                    if color:
                        self._execute_bets(user_id, color)
            
            def on_result(msg, is_win):
                self._sync_send_message(user_id, msg)
                if self.user_sessions.get(user_id, {}).get('auto_betting_active'):
                    self._update_bet_on_result(user_id, is_win)
                    self._show_balances(user_id)
            
            self.global_polling.register_user(user_id, strategy, on_status, on_prediction, on_result)
            
            self.user_sessions[user_id] = {
                'mode': 'auto',
                'accounts': accounts,
                'auto_betting_active': False,
                'strategy': strategy,
                'bet_config': {
                    'initial_bet': 0.1,
                    'current_bet': 0.1,
                    'max_bet': 10.0,
                    'max_losses': 5,
                    'use_martingale': False,
                }
            }
            await self.show_betting_config(update, user_id)
        else:
            await update.message.reply_text("❌ No se pudo conectar ninguna cuenta.")
        
        context.user_data['awaiting_credentials'] = False
    
    def _execute_bets(self, user_id: int, color: str):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        
        for account in session.get('accounts', []):
            try:
                if not account.betting_active:
                    continue
                if account.balance <= 0:
                    self._sync_send_message(user_id, f"⚠️ {account.username}: Sin fondos")
                    account.betting_active = False
                    continue
                if account.current_bet > account.balance:
                    self._sync_send_message(user_id, f"⚠️ {account.username}: Apuesta ${account.current_bet:.2f} > Balance")
                    continue
                
                success, msg = account.place_bet(color, account.current_bet)
                if success:
                    self._sync_send_message(user_id, f"✅ {account.username}: ${account.current_bet:.2f} a {color.upper()}")
                else:
                    self._sync_send_message(user_id, f"❌ {account.username}: {msg}")
            except Exception as e:
                print(f"Error en apuesta: {e}")
    
    def _update_bet_on_result(self, user_id: int, won: bool):
        session = self.user_sessions.get(user_id)
        if not session:
            return
        
        for account in session.get('accounts', []):
            if not account.betting_active:
                continue
            
            if won:
                account.wins += 1
                account.reset_bet()
                self._sync_send_message(user_id, f"💰 {account.username}: WIN - Reiniciada a ${account.current_bet:.2f}")
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
        
        msg = "💰 SALDOS ACTUALIZADOS\n\n"
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
            [InlineKeyboardButton(f"🎲 Modo: {'Martingala (x2)' if config['use_martingale'] else 'Agresivo (x2+1)'}", callback_data='cfg_mode')],
            [InlineKeyboardButton("📊 Ver Balances", callback_data='view_balances')],
            [InlineKeyboardButton("▶️ INICIAR AUTO-BET", callback_data='start_autobet')],
            [InlineKeyboardButton("◀️ Volver", callback_data='back_to_start')]
        ]
        
        msg = (
            f"⚙️ CONFIGURACIÓN DE APUESTAS\n\n"
            f"💰 Apuesta actual: ${config['current_bet']}\n"
            f"🎲 Modo: {'Martingala (x2)' if config['use_martingale'] else 'Agresivo (x2+1)'}\n\n"
            f"Agresivo (x2+1): ej: 0.1 → 0.3 → 0.7 → 1.5..."
        )
        
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def cfg_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        current = self.user_sessions[user_id]['bet_config']['use_martingale']
        self.user_sessions[user_id]['bet_config']['use_martingale'] = not current
        for acc in self.user_sessions[user_id]['accounts']:
            acc.use_martingale = not current
        mode = "Martingala (x2)" if not current else "Agresivo (x2+1)"
        await update.callback_query.answer(f"Modo cambiado a {mode}")
        await self.show_betting_config(update, user_id)
    
    async def cfg_initial(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("💰 Envía nuevo monto inicial (mínimo 0.1):\nEj: 0.5")
        context.user_data['awaiting_initial_bet'] = True
    
    async def cfg_max_bet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("📈 Envía monto máximo:\nEj: 10.0")
        context.user_data['awaiting_max_bet'] = True
    
    async def cfg_max_losses(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🛑 Envía número máximo de pérdidas:\nEj: 5")
        context.user_data['awaiting_max_losses'] = True
    
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
            if value < 1 or value > 20:
                await update.message.reply_text("❌ Valor entre 1 y 20")
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
            acc.consecutive_losses = 0
            acc.betting_active = True
        
        self.user_sessions[user_id]['auto_betting_active'] = True
        
        modo_texto = "Martingala (x2)" if config['use_martingale'] else "Agresivo (x2+1)"
        
        await update.callback_query.edit_message_text(
            f"✅ AUTO-BET ACTIVADO\n\n"
            f"📊 Estrategia: {self.user_sessions[user_id].get('strategy', 'standard').upper()}\n"
            f"💰 Inicial: ${config['initial_bet']}\n"
            f"📈 Máximo: ${config['max_bet']}\n"
            f"🛑 Max Losses: {config['max_losses']}\n"
            f"🎲 Modo apuesta: {modo_texto}\n"
            f"📊 Cuentas activas: {len(self.user_sessions[user_id]['accounts'])}\n\n"
            f"Usa /stop para detener."
        )
    
    async def view_balances(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        session = self.user_sessions.get(user_id)
        if not session:
            await update.callback_query.answer("No hay sesión")
            return
        
        msg = "💰 BALANCES\n\n"
        for acc in session.get('accounts', []):
            acc.get_balance()
            msg += f"• {acc.username}: ${acc.balance:.2f}\n"
        await update.callback_query.edit_message_text(msg)
    
    async def buy_license(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🎁 PRUEBA 24h (GRATIS)", callback_data='plan_test_24h')],
            [InlineKeyboardButton("📅 Estándar 30d - 10 USDT", callback_data='plan_standard')],
            [InlineKeyboardButton("📊 Peak-Break 30d - 15 USDT", callback_data='plan_peakbreak')],
            [InlineKeyboardButton("👻 Peak-Ghost 30d - 20 USDT", callback_data='plan_ghost')],
            [InlineKeyboardButton("👥 Multiuser 30d - 45 USDT", callback_data='plan_multiuser')]
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
                await query.edit_message_text("❌ Ya tienes una licencia activa.")
                return
            if self.license_manager.activate_license(user_id, "test_24h"):
                await query.edit_message_text(
                    "🎁 LICENCIA DE PRUEBA ACTIVADA\n\n"
                    "✅ Duración: 24 horas\n"
                    "✅ Modo: Estándar\n\n"
                    "Usa /start para comenzar."
                )
            else:
                await query.edit_message_text("❌ Error al activar.")
            return
        
        plan = LICENSE_PLANS.get(plan_id)
        if not plan:
            await query.edit_message_text("❌ Plan inválido")
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
            "Envía la imagen con el TXID visible."
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
            else:
                await update.message.reply_text("❌ Envía una imagen con el comprobante.")
                return
            
            await update.message.reply_text("✅ Comprobante enviado. Será verificado por un administrador.")
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
            await query.edit_message_text(
                f"📜 INFORMACIÓN DE LICENCIA\n\n"
                f"📋 Plan: {LICENSE_PLANS[data['plan']]['name']}\n"
                f"👥 Modo asignado: {data.get('mode', 'standard').upper()}\n"
                f"🔢 Máx cuentas: {data.get('max_users', 1)}\n"
                f"📅 Activada: {datetime.fromisoformat(data['activated']).strftime('%Y-%m-%d')}\n"
                f"⏰ Expira: {expiry.strftime('%Y-%m-%d')}\n"
                f"📆 Días restantes: {days}"
            )
        else:
            await query.edit_message_text("❌ Sin licencia activa. Usa /start")
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id in self.user_sessions:
            self.user_sessions[user_id]['auto_betting_active'] = False
            self.global_polling.unregister_user(user_id)
            del self.user_sessions[user_id]
        await update.message.reply_text("⏹️ Auto-bot detenido. Usa /start para volver.")
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("❌ Operación cancelada.")
    
    async def validate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ No autorizado")
            return
        
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "📋 USO: /validar USER_ID PLAN\n\n"
                "PLANES:\n"
                "• test_24h - Prueba 24h\n"
                "• standard - Estándar 30d\n"
                "• peakbreak - Peak-Break 30d\n"
                "• ghost - Peak-Ghost 30d\n"
                "• multiuser - Multiuser 30d"
            )
            return
        
        user_id = int(args[0])
        plan = args[1]
        
        if self.license_manager.activate_license(user_id, plan):
            await update.message.reply_text(f"✅ Licencia {plan} activada para usuario {user_id}")
            await self._send_message(user_id, f"🎉 LICENCIA ACTIVADA!\n\n📦 Plan: {LICENSE_PLANS[plan]['name']}\n✅ Ya puedes usar el bot.\n\nUsa /start para comenzar.")
        else:
            await update.message.reply_text("❌ Plan inválido.")
    
    async def back_to_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.start_command(update, context)
    
    async def handle_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('awaiting_strategy_selection'):
            await self.select_strategy(update, context)
        elif context.user_data.get('awaiting_credentials'):
            await self.process_credentials(update, context)
        elif context.user_data.get('awaiting_initial_bet'):
            await self.process_initial_bet(update, context)
        elif context.user_data.get('awaiting_max_bet'):
            await self.process_max_bet(update, context)
        elif context.user_data.get('awaiting_max_losses'):
            await self.process_max_losses(update, context)
        elif context.user_data.get('awaiting_payment_proof'):
            if update.message.photo:
                await self.handle_payment_proof(update, context)
            else:
                await update.message.reply_text("❌ Envía una imagen con el comprobante.")
    
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
        self.application.add_handler(CallbackQueryHandler(self.cfg_mode, pattern='cfg_mode'))
        self.application.add_handler(CallbackQueryHandler(self.start_autobet, pattern='start_autobet'))
        self.application.add_handler(CallbackQueryHandler(self.view_balances, pattern='view_balances'))
        self.application.add_handler(CallbackQueryHandler(self.license_info, pattern='license_info'))
        self.application.add_handler(CallbackQueryHandler(self.back_to_start, pattern='back_to_start'))
        
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_messages))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_messages))
        
        print("=" * 50)
        print("🤖 PREDICTOR PRO BOT INICIADO (CORREGIDO)")
        print("=" * 50)
        print("📊 MODOS DE ESTRATEGIA:")
        print("  • ESTÁNDAR - Último color + espera 1,2,1,2...")
        print("  • PEAK-BREAK - Entrar después de 2 LOSS seguidos")
        print("  • PEAK-GHOST - WIN→LOSS activa espera hasta WIN+LOSS")
        print("=" * 50)
        print("💰 PLANES:")
        print("  • Prueba 24h: 0 USDT")
        print("  • Estándar 30d: 10 USDT")
        print("  • Peak-Break 30d: 15 USDT")
        print("  • Peak-Ghost 30d: 20 USDT")
        print("  • Multiuser 30d: 45 USDT (5 cuentas, elige modo)")
        print("=" * 50)
        print("🎲 MODOS DE APUESTA:")
        print("  • Martingala: x2")
        print("  • Agresivo: (x2)+1")
        print("=" * 50)
        
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    print("🚀 INICIANDO PREDICTOR PRO BOT...")
    bot = PredictionBot(BOT_TOKEN)
    bot.run()
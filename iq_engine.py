import time
import datetime
import requests
import pandas as pd
import numpy as np
from colorama import Fore, Style, init
from iqoptionapi.stable_api import IQ_Option
import config

init(autoreset=True)

class IQEngine:
    def __init__(self):
        self.api = IQ_Option(config.IQ_EMAIL, config.IQ_PASSWORD)
        self.connected = False
        self.last_trade_time = 0

    def send_telegram(self, message):
        if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
            return
        url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID, 
            "text": message, 
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Telegram error: {e}")

    def connect(self):
        print(f"{Fore.CYAN}[*] Connecting to IQ Option...{Style.RESET_ALL}")
        check, reason = self.api.connect()
        if not check:
            print(f"{Fore.RED}[!] Connection failed: {reason}{Style.RESET_ALL}")
            return False
        
        self.api.change_balance(config.ACCOUNT_TYPE)
        balance = self.api.get_balance()
        print(f"{Fore.GREEN}[+] Connected. {config.ACCOUNT_TYPE} Balance: ${balance:.2f}{Style.RESET_ALL}")
        self.send_telegram(f"✅ *Bot Connected*\nMode: {config.ACCOUNT_TYPE}\nBalance: ${balance:.2f}")
        self.connected = True
        return True

    def get_signal(self):
        candles = self.api.get_candles(config.ASSET, config.TIMEFRAME, 100, time.time())
        if len(candles) < 50:
            return "NEUTRAL", 0

        df = pd.DataFrame(candles)
        df = df.rename(columns={"max": "high", "min": "low"})
        close = df["close"]

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()

        # EMA
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()

        # Signals
        rsi_buy = current_rsi < 30
        rsi_sell = current_rsi > 70
        
        macd_buy = macd.iloc[-2] < signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]
        macd_sell = macd.iloc[-2] > signal.iloc[-2] and macd.iloc[-1] < signal.iloc[-1]
        
        ema_buy = ema9.iloc[-2] < ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]
        ema_sell = ema9.iloc[-2] > ema21.iloc[-2] and ema9.iloc[-1] < ema21.iloc[-1]

        if (rsi_buy + macd_buy + ema_buy) >= 2:
            return "CALL", current_rsi
        elif (rsi_sell + macd_sell + ema_sell) >= 2:
            return "PUT", current_rsi
        
        return "NEUTRAL", current_rsi

    def run_loop(self):
        if not self.connect():
            return

        mode_str = "PAPER" if config.PAPER_MODE else "LIVE"
        print(f"{Fore.YELLOW}[*] Engine started. Mode: {mode_str}{Style.RESET_ALL}")
        
        try:
            while True:
                direction, rsi = self.get_signal()
                
                if direction != "NEUTRAL":
                    elapsed = time.time() - self.last_trade_time
                    if elapsed >= config.COOLDOWN_SECONDS:
                        now_str = datetime.datetime.now().strftime('%H:%M:%S')
                        print(f"\n{Fore.CYAN}[{now_str}] SIGNAL: {direction} | RSI: {rsi:.2f}{Style.RESET_ALL}")
                        
                        msg = f"🚨 *{mode_str} SIGNAL*\nAsset: {config.ASSET}\nDirection: *{direction}*\nAmount: ${config.TRADE_AMOUNT}\nTime: {now_str}"
                        self.send_telegram(msg)

                        if config.PAPER_MODE:
                            print(f"{Fore.YELLOW}[PAPER] Would have placed {direction} trade for ${config.TRADE_AMOUNT}{Style.RESET_ALL}")
                        else:
                            print(f"{Fore.GREEN}[LIVE] Executing {direction}...{Style.RESET_ALL}")
                            check, result = self.api.buy(config.TRADE_AMOUNT, config.ASSET, direction, config.TIMEFRAME)
                            if check:
                                print(f"[+] Trade success. ID: {result}")
                            else:
                                print(f"{Fore.RED}[!] Trade failed. Reason: {result}{Style.RESET_ALL}")

                        self.last_trade_time = time.time()
                        # Sleep through the expiry to avoid double firing
                        time.sleep(config.TIMEFRAME)
                
                time.sleep(2)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}[*] Stopped.{Style.RESET_ALL}")
            self.send_telegram("⏹ *Bot Stopped*")

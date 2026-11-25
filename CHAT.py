"""
Клієнт чату на customtkinter + WebSocket.

Як це працює:
- Вікно (GUI) працює в головному потоці Tkinter.
- Підключення до сервера та отримання повідомлень відбувається
  у фоновому потоці (connection_loop), щоб інтерфейс не "зависав".
- Будь-яке оновлення інтерфейсу з фонового потоку робиться через
  self.after(0, ...), бо Tkinter не потокобезпечний.
- Формат повідомлень між клієнтом і сервером:
    TEXT@ім'я@текст
    IMAGE@ім'я@назва_файлу@дані
"""

import socket
import threading
import time
from urllib.parse import urlparse
from urllib.request import getproxies

import websocket          # бібліотека websocket-client
import customtkinter as ctk


# Адреса WebSocket-сервера (тунель ngrok)
SERVER_URL = "wss://corncob-greasily-semifinal.ngrok-free.dev"


# ---------------- НАЛАШТУВАННЯ ----------------

# Початкова тема та колір кнопок/елементів
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def proxy_settings():
    """
    Зчитує системні налаштування проксі та повертає їх
    у форматі, який розуміє websocket.create_connection().
    Якщо проксі немає — повертає порожній словник.
    """
    proxies = getproxies()
    proxy = proxies.get("https") or proxies.get("http")

    if not proxy:
        return {}

    # Якщо схема (http://) не вказана — додаємо, щоб urlparse коректно розібрав адресу
    p = urlparse(
        proxy if "://" in proxy else "http://" + proxy
    )

    if not p.hostname:
        return {}

    return {
        "http_proxy_host": p.hostname,
        "http_proxy_port": p.port or 8080,   # порт за замовчуванням
        "proxy_type": "http"
    }


# ---------------- ГОЛОВНЕ ВІКНО ----------------

class MainWindow(ctk.CTk):

    def __init__(self):
        super().__init__()

        # Параметри вікна
        self.title("My Chat")
        self.geometry("750x550")
        self.minsize(450, 350)

        # Стан користувача та з'єднання
        self.username = "Oleksii"
        self.sock = None                        # поточне WebSocket-з'єднання
        self.connected = False                  # чи є активне з'єднання
        self.closing = False                    # прапорець закриття програми
        self.socket_lock = threading.Lock()     # захист сокета від одночасного доступу з різних потоків
        self.connection_thread = None  # потік підключення/перепідключення

        self.emoji_window = None  # вікно панелі емодзі
        self.connection_thread = None           # потік підключення/перепідключення

        # Бокове меню спочатку показане (див. create_interface), тому True
        self.menu_open = False

        # Коректне закриття при натисканні на хрестик
        self.protocol("WM_DELETE_WINDOW", self.close_app)

        self.create_interface()

        self.connect_server()

    # ---------------- ІНТЕРФЕЙС ----------------

    def create_interface(self):
        """Створює всі елементи вікна: бокове меню, шапку, чат і поле введення."""

        # Головний контейнер: колонка 1 (чат) розтягується
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ----- Бокове меню (налаштування) -----
        self.menu = ctk.CTkFrame(
            self,
            width=180,
            corner_radius=0
        )

        self.menu.grid(
            row=0,
            column=0,
            sticky="ns"
        )

        # Забороняємо меню підлаштовувати розмір під вміст — тримаємо ширину 180
        self.menu.grid_propagate(False)

        ctk.CTkLabel(
            self.menu,
            text="⚙ Налаштування",
            font=("Arial", 18, "bold")
        ).pack(pady=25)

        ctk.CTkLabel(
            self.menu,
            text="Ім'я користувача"
        ).pack(pady=(10, 5))

        # Поле для зміни імені
        self.name_entry = ctk.CTkEntry(
            self.menu,
            placeholder_text="Нове ім'я"
        )

        self.name_entry.pack(
            padx=15,
            fill="x"
        )

        self.name_entry.insert(0, self.username)

        ctk.CTkButton(
            self.menu,
            text="Змінити ім'я",
            command=self.change_username
        ).pack(padx=15, pady=10, fill="x")

        ctk.CTkLabel(
            self.menu,
            text="Тема оформлення"
        ).pack(pady=(25, 5))

        # Випадаючий список вибору теми
        self.theme_menu = ctk.CTkOptionMenu(
            self.menu,
            values=["Темна", "Світла", "Системна"],
            command=self.change_theme
        )

        self.theme_menu.pack(
            padx=15,
            fill="x"
        )

        self.theme_menu.set("Темна")

        # ----- Основна частина (праворуч від меню) -----
        self.main_frame = ctk.CTkFrame(
            self,
            corner_radius=0
        )

        self.main_frame.grid(
            row=0,
            column=1,
            sticky="nsew"
        )

        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(1, weight=1)   # рядок із чатом розтягується

        # ----- Верхня панель -----
        self.header = ctk.CTkFrame(
            self.main_frame,
            height=55
        )

        self.header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=10,
            pady=10
        )

        self.header.grid_columnconfigure(1, weight=1)

        # Кнопка показу/приховування бокового меню
        self.menu_button = ctk.CTkButton(
            self.header,
            text="☰",
            width=45,
            command=self.toggle_menu
        )

        self.menu_button.grid(
            row=0,
            column=0,
            padx=5,
            pady=5
        )

        self.title_label = ctk.CTkLabel(
            self.header,
            text="MY CHAT",
            font=("Arial", 20, "bold")
        )

        self.title_label.grid(
            row=0,
            column=1,
            padx=10
        )

        # Індикатор стану з'єднання (колір і текст змінюються в connection_status)
        self.status_label = ctk.CTkLabel(
            self.header,
            text="● Відключено",
            text_color="#E74C3C"
        )

        self.status_label.grid(
            row=0,
            column=2,
            padx=10
        )

        # ----- Поле чату -----
        # state="disabled" — користувач не може редагувати історію вручну
        self.chat_field = ctk.CTkTextbox(
            self.main_frame,
            font=("Arial", 14),
            wrap="word",
            state="disabled"
        )

        self.chat_field.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=10,
            pady=5
        )

        # ----- Нижня панель введення -----
        self.input_frame = ctk.CTkFrame(
            self.main_frame
        )

        self.input_frame.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=10,
            pady=10
        )

        self.input_frame.grid_columnconfigure(0, weight=1)

        self.message_entry = ctk.CTkEntry(
            self.input_frame,
            height=42,
            font=("Arial", 14),
            placeholder_text="Введіть повідомлення..."
        )

        self.message_entry.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=5,
            pady=5
        )

        # Відправлення повідомлення клавішею Enter
        self.message_entry.bind(
            "<Return>",
            self.enter_send
        )

        self.send_button = ctk.CTkButton(
            self.input_frame,
            text="➤",
            width=55,
            height=42,
            font=("Arial", 18, "bold"),
            command=self.send_message
        )

        self.send_button.grid(
            row=0,
            column=1,
            padx=5,
            pady=5
        )

        # Курсор одразу в полі введення
        self.message_entry.focus()

    # ---------------- ТЕМИ ----------------

    def change_theme(self, choice):
        """Перемикає тему оформлення за вибором у списку."""

        themes = {
            "Темна": "dark",
            "Світла": "light",
            "Системна": "system"
        }

        ctk.set_appearance_mode(themes[choice])

    # ---------------- БОКОВЕ МЕНЮ ----------------

    def toggle_menu(self):
        """Показує або ховає бокове меню."""

        self.menu_open = not self.menu_open

        if self.menu_open:
            self.menu.grid()          # повернути на місце
        else:
            self.menu.grid_remove()   # сховати (з пам'яттю позиції)

    # ---------------- ЗМІНА ІМЕНІ ----------------

    def change_username(self):
        """Змінює ім'я користувача та повідомляє про це чат."""

        new_name = self.name_entry.get().strip()

        # Порожнє ім'я не дозволяємо
        if not new_name:
            self.add_message("[SYSTEM] Ім'я не може бути порожнім")
            return

        old_name = self.username
        self.username = new_name

        # Локальне повідомлення для самого користувача
        self.add_message(
            f"[SYSTEM] Ви змінили ім'я: {old_name} → {new_name}"
        )

        # Повідомляємо інших учасників, якщо є з'єднання
        if self.connected:
            self.send_raw(
                f"TEXT@{self.username}@[SYSTEM] {old_name} тепер {new_name}"
            )

    # ---------------- ПІДКЛЮЧЕННЯ ----------------

    def connect_server(self):
        """Запускає фоновий потік підключення (не більше одного одночасно)."""

        # Запускаємо лише один потік, який відповідає за підключення
        # та автоматичне перепідключення.
        if self.closing:
            return

        if self.connection_thread and self.connection_thread.is_alive():
            return

        # daemon=True — потік завершиться разом із програмою
        self.connection_thread = threading.Thread(
            target=self.connection_loop,
            daemon=True
        )
        self.connection_thread.start()

    def connection_loop(self):
        """
        Нескінченний цикл: підключитися → слухати повідомлення →
        при розриві почекати 5 секунд і спробувати знову.
        Працює у фоновому потоці.
        """

        while not self.closing:
            sock = None

            try:
                # Показуємо статус "Підключення..." (через after — це GUI)
                self.after(0, self.connection_status, "connecting")

                sock = websocket.create_connection(
                    SERVER_URL,
                    # Заголовок, щоб ngrok не показував сторінку-попередження
                    header=[
                        "ngrok-skip-browser-warning: true"
                    ],
                    timeout=10,
                    enable_multithread=True,    # дозволяє send() і recv() з різних потоків
                    **proxy_settings()
                )

                # Тайм-аут потрібен лише для встановлення з'єднання.
                # Не відключаємо користувача, якщо в чаті довго тихо.
                sock.settimeout(None)

                # Зберігаємо сокет під замком; якщо програму вже закривають — виходимо
                with self.socket_lock:
                    if self.closing:
                        sock.close()
                        return
                    self.sock = sock
                    self.connected = True

                self.after(0, self.connection_status, True)
                self.after(
                    0,
                    self.add_message,
                    "[SYSTEM] Підключено до сервера"
                )

                # Повідомляємо всім, що користувач приєднався
                self.send_raw(
                    f"TEXT@{self.username}@[SYSTEM] "
                    f"{self.username} приєднався(лась) до чату!"
                )

                # Блокується тут, поки з'єднання живе
                self.recv_message(sock)

            except Exception as e:
                if not self.closing:
                    self.after(
                        0,
                        self.add_message,
                        f"[SYSTEM] Помилка підключення: {e}"
                    )

            finally:
                # Прибираємо стан з'єднання та закриваємо сокет
                with self.socket_lock:
                    if self.sock is sock:
                        self.sock = None
                    self.connected = False

                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

                # Якщо програма не закривається — пауза і нова спроба
                if not self.closing:
                    self.after(0, self.connection_status, False)
                    self.after(
                        0,
                        self.add_message,
                        "[SYSTEM] З'єднання втрачено. "
                        "Повторна спроба через 5 секунд..."
                    )
                    time.sleep(5)

    def connection_status(self, status):
        """
        Оновлює індикатор у шапці.
        status: "connecting" — підключення, True — підключено, False — відключено.
        """

        if status == "connecting":
            self.status_label.configure(
                text="● Підключення...",
                text_color="#F1C40F"      # жовтий
            )
        elif status:
            self.status_label.configure(
                text="● Підключено",
                text_color="#2ECC71"      # зелений
            )
        else:
            self.status_label.configure(
                text="● Відключено",
                text_color="#E74C3C"      # червоний
            )

    # ---------------- ПОВІДОМЛЕННЯ ----------------

    def enter_send(self, event):
        """Обробник клавіші Enter у полі введення."""

        self.send_message()
        return "break"    # забороняємо стандартну обробку події

    def send_message(self):
        """Відправляє введений текст на сервер."""

        message = self.message_entry.get().strip()

        # Порожні повідомлення ігноруємо
        if not message:
            return

        if not self.connected or not self.sock:
            self.add_message(
                "[SYSTEM] Немає з'єднання із сервером"
            )
            return

        # Формат: TEXT@ім'я@текст
        data = f"TEXT@{self.username}@{message}"

        try:
            self.sock.send(data)
            self.add_message(f"{self.username}: {message}")
            # Очищаємо поле лише після успішного відправлення
            self.message_entry.delete(0, "end")

        except Exception as e:

            self.add_message(
                f"[SYSTEM] Помилка відправлення: {e}"
            )

    def send_raw(self, data):
        """
        Потокобезпечне відправлення сирого рядка (використовується з фонових потоків).
        Повертає True, якщо відправлено успішно.
        """

        try:
            with self.socket_lock:
                if self.connected and self.sock:
                    self.sock.send(data)
                    return True
        except Exception as e:
            if not self.closing:
                self.after(
                    0,
                    self.add_message,
                    f"[SYSTEM] Помилка відправлення: {e}"
                )
        return False

    # ---------------- ОТРИМАННЯ ----------------

    def recv_message(self, sock=None):
        """Читає повідомлення із сокета, доки з'єднання активне."""

        # Використовуємо саме той сокет, який відкрив цей потік.
        if sock is None:
            sock = self.sock

        while self.connected and not self.closing and self.sock is sock:

            try:
                data = sock.recv()

                # Порожні дані означають, що сервер закрив з'єднання
                if not data:
                    break

                # Передаємо обробку в головний потік (GUI)
                self.after(
                    0,
                    self.handle_message,
                    data.strip()
                )

            except Exception:
                break

        if not self.closing:
            self.after(
                0,
                self.add_message,
                "[SYSTEM] З'єднання із сервером розірвано"
            )

    def handle_message(self, line):
        """Розбирає отриманий рядок за типом і виводить його в чат."""

        if not line:
            return

        # Розділяємо максимум на 4 частини, щоб '@' у тексті не ламав розбір
        parts = line.split("@", 3)

        msg_type = parts[0]

        if msg_type == "TEXT" and len(parts) >= 3:

            author = parts[1]
            message = parts[2]

            self.add_message(
                f"{author}: {message}"
            )

        elif msg_type == "IMAGE" and len(parts) >= 4:

            # Зображення поки лише повідомляємо текстом (без показу самої картинки)
            author = parts[1]
            filename = parts[2]

            self.add_message(
                f"{author} надіслав(ла) зображення: {filename}"
            )

        else:
            # Невідомий формат — показуємо як є
            self.add_message(line)

    # ---------------- ВІДОБРАЖЕННЯ ----------------

    def add_message(self, text):
        """Додає рядок у кінець чату та прокручує вниз."""

        # Тимчасово вмикаємо редагування, бо поле за замовчуванням заблоковане
        self.chat_field.configure(state="normal")

        self.chat_field.insert(
            "end",
            text + "\n"
        )

        self.chat_field.see("end")    # автопрокрутка до останнього повідомлення

        self.chat_field.configure(state="disabled")

    # ---------------- ЗАКРИТТЯ ----------------

    def close_app(self):
        """Акуратно закриває з'єднання та вікно."""

        # Зупиняє цикл перепідключення
        self.closing = True

        with self.socket_lock:
            self.connected = False
            sock = self.sock
            self.sock = None

        try:
            if sock:
                sock.close()
        except Exception:
            pass

        self.destroy()


# ---------------- ЗАПУСК ----------------

if __name__ == "__main__":

    win = MainWindow()
    win.mainloop()
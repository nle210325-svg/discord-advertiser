from colorama import Fore, Style, init
from os import system
import time
from pystyle import Colors
from datetime import datetime
from collections.abc import Iterable

class Logger:

    @staticmethod
    def Log(work_done, message, color, **kwargs):
        timestamp = datetime.fromtimestamp(time.time()).strftime("%H:%M:%S")
        output_message = f"{Colors.dark_gray}{timestamp} » {color}{work_done} {Colors.dark_gray}•{Colors.white} {message}{Colors.dark_gray} \u2794 "

        for key, value in kwargs.items():
            formatted_key = f"{Colors.white}{key}"
            if isinstance(value, str):
                formatted_value = f"[{color}{value}{Colors.white}]"
            elif isinstance(value, Iterable) and not isinstance(value, str):
                formatted_value = f"[{', '.join([f'{color}{v}{Colors.white}' for v in value])}]"
            else:
                formatted_value = f"[{color}{value}{Colors.white}]"
            output_message += f" {formatted_key} {formatted_value}{Colors.dark_gray} \u2794 "

        output_message = output_message[:-3]
        output_message += f" {Colors.white}{Style.RESET_ALL}"

        try:
            print(output_message)

        except:
            output_message = output_message.replace('\u2794', '->')
            print(output_message)
        
    @staticmethod
    def w_Input(message, **kwargs):
        timestamp = f"{Fore.RESET}{Fore.LIGHTBLACK_EX}{datetime.now().strftime('%H:%M:%S')}{Fore.RESET}"
        return input(f"{Fore.LIGHTBLACK_EX}[{Fore.MAGENTA}{timestamp}{Fore.LIGHTBLACK_EX}] {Fore.LIGHTMAGENTA_EX}[INPUT] {Fore.RESET}{message}")
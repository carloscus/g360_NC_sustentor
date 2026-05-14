#!/usr/bin/env python3
"""
G360 CustomTkinter Desktop Application
Modern GUI with G360 theme
"""

import customtkinter as ctk
from pathlib import Path
import json


VERSION = "1.0.0"
APP_NAME = "G360 Desktop"


def load_skill():
    skill_path = Path(__file__).parent / "core" / "skill.json"
    if skill_path.exists():
        with open(skill_path) as f:
            return json.load(f)
    return {
        "colors": {
            "bg": "#0b1220",
            "surface": "#1a2332",
            "accent": "#00d084"
        }
    }


class G360App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        skill = load_skill()
        colors = skill.get("colors", {})
        
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")
        
        self.title(APP_NAME)
        self.geometry("800x600")
        self.configure(fg_color=colors.get("bg", "#0b1220"))
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.create_header(colors)
        self.create_content(colors)
        self.create_footer()
    
    def create_header(self, colors):
        self.header = ctk.CTkFrame(self, fg_color=colors.get("surface", "#1a2332"))
        self.header.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        
        title = ctk.CTkLabel(
            self.header,
            text=f"{APP_NAME} v{VERSION}",
            font=("Segoe UI", 20, "bold"),
            text_color=colors.get("accent", "#00d084")
        )
        title.pack(side="left", padx=20, pady=10)
        
        btn_settings = ctk.CTkButton(
            self.header,
            text="Settings",
            fg_color="transparent",
            border_width=1,
            border_color=colors.get("accent", "#00d084"),
            text_color=colors.get("accent", "#00d084")
        )
        btn_settings.pack(side="right", padx=20)
    
    def create_content(self, colors):
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        
        card = ctk.CTkFrame(
            self.content,
            fg_color=colors.get("surface", "#1a2332"),
            corner_radius=12
        )
        card.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        label = ctk.CTkLabel(
            card,
            text="G360 Application\nReady to build",
            font=("Segoe UI", 16),
            text_color="#f0f4f8"
        )
        label.pack(pady=40)
        
        btn_primary = ctk.CTkButton(
            card,
            text="Start",
            fg_color=colors.get("accent", "#00d084"),
            text_color="#0b1220",
            hover_color="#00b070"
        )
        btn_primary.pack(pady=20)
    
    def create_footer(self):
        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        
        status = ctk.CTkLabel(
            self.footer,
            text="G360 Desktop App",
            text_color="#94a3b8",
            font=("Segoe UI", 12)
        )
        status.pack(side="left", padx=20)


def main():
    app = G360App()
    app.mainloop()


if __name__ == "__main__":
    main()
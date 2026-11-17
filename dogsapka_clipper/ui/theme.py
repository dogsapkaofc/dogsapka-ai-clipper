"""Visual theme "Modern glass": deep dark background with soft purple/cyan light, translucent surfaces.

Colour constants stay plain hex (they are also used by QPainter); translucency lives in the QSS only.
"""
from __future__ import annotations

BG = "#07070D"
BG2 = "#0D0D17"
SURFACE = "#13131F"
SURFACE2 = "#1A1A29"
BORDER = "#252538"
TEXT = "#F1F1F8"
MUTED = "#8C8CA6"
PURPLE = "#8B5CF6"
PURPLE2 = "#A78BFA"
CYAN = "#22D3EE"
GREEN = "#34D399"
RED = "#F43F5E"
AMBER = "#FBBF24"

LEVEL_COLORS = {"info": CYAN, "success": GREEN, "warning": AMBER, "error": RED}

# translucent layers (QSS only)
GLASS = "rgba(255,255,255,0.035)"
GLASS_HOVER = "rgba(255,255,255,0.06)"
GLASS_BORDER = "rgba(255,255,255,0.07)"
GLASS_BORDER_HI = "rgba(167,139,250,0.45)"
FIELD = "rgba(255,255,255,0.045)"

QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue", "Inter", "Noto Sans", sans-serif;
     font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Root {{ background: {BG}; border: 1px solid {GLASS_BORDER}; border-radius: 16px; }}
QWidget#Sidebar {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0E0C1C, stop:0.55 #0A0A14, stop:1 #08080F);
    border-right: 1px solid rgba(255,255,255,0.05);
    border-top-left-radius: 16px; border-bottom-left-radius: 16px; }}
QWidget#Content {{
    background: qradialgradient(cx:0.92, cy:-0.05, radius:0.95, fx:0.92, fy:-0.05,
        stop:0 rgba(139,92,246,0.20), stop:0.45 rgba(34,211,238,0.045), stop:1 {BG});
    border-top-right-radius: 16px; border-bottom-right-radius: 16px; }}
QWidget#NavIndicator {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(139,92,246,0.34), stop:1 rgba(34,211,238,0.07));
    border: 1px solid rgba(167,139,250,0.28); border-radius: 12px; }}
QWidget#NavBar {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {PURPLE2}, stop:1 {CYAN}); border-radius: 2px; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 21pt; font-weight: 700; letter-spacing: -0.3px; }}
QLabel#H2 {{ font-size: 12.5pt; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Brand {{ font-size: 13pt; font-weight: 800; letter-spacing: 1.5px; }}
QLabel#StatValue {{ font-size: 23pt; font-weight: 700; letter-spacing: -0.5px; }}
QFrame#Card {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,0.055), stop:1 rgba(255,255,255,0.025));
               border: 1px solid {GLASS_BORDER}; border-radius: 18px; }}
QFrame#Card[hover="true"] {{ border-color: {GLASS_BORDER_HI}; }}
QPushButton {{ background: {GLASS}; border: 1px solid {GLASS_BORDER}; border-radius: 11px; padding: 8px 15px; }}
QPushButton:hover {{ background: {GLASS_HOVER}; border-color: rgba(167,139,250,0.55); }}
QPushButton:pressed {{ background: rgba(139,92,246,0.18); }}
QPushButton:disabled {{ color: #4E4E64; border-color: rgba(255,255,255,0.04); }}
QPushButton#Primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #9D6BFF, stop:0.55 {PURPLE}, stop:1 #6D28D9);
                       border: 1px solid rgba(255,255,255,0.14); font-weight: 600; }}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #B794FF, stop:0.55 {PURPLE2}, stop:1 {PURPLE}); }}
QPushButton#Primary:pressed {{ background: #6D28D9; }}
QPushButton#Accent {{ background: rgba(34,211,238,0.06); border: 1px solid rgba(34,211,238,0.55); color: {CYAN}; }}
QPushButton#Accent:hover {{ background: rgba(34,211,238,0.14); }}
QPushButton#Danger {{ background: rgba(244,63,94,0.05); border: 1px solid rgba(244,63,94,0.35); color: {RED}; }}
QPushButton#Danger:hover {{ background: rgba(244,63,94,0.16); border-color: {RED}; }}
QPushButton#Nav {{ background: transparent; border: none; border-radius: 12px; text-align: left; padding: 11px 14px;
                   color: {MUTED}; font-weight: 500; }}
QPushButton#Nav:hover {{ background: rgba(255,255,255,0.04); color: {TEXT}; }}
QPushButton#Nav:checked {{ background: transparent; color: {TEXT}; font-weight: 600; }}
QPushButton#TitleBtn {{ background: transparent; border: none; border-radius: 8px; padding: 4px 10px; }}
QPushButton#TitleBtn:hover {{ background: rgba(255,255,255,0.07); }}
QPushButton#CloseBtn {{ background: transparent; border: none; border-radius: 8px; padding: 4px 10px; }}
QPushButton#CloseBtn:hover {{ background: {RED}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {FIELD}; border: 1px solid {GLASS_BORDER}; border-radius: 10px; padding: 7px 10px;
    selection-background-color: {PURPLE}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QPlainTextEdit:hover {{
    border-color: rgba(255,255,255,0.13); }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {PURPLE}; background: rgba(139,92,246,0.06); }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 18px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px;
                               selection-background-color: rgba(139,92,246,0.35); outline: none; }}
QCheckBox {{ spacing: 9px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.16);
                        background: {FIELD}; }}
QCheckBox::indicator:hover {{ border-color: {PURPLE2}; }}
QCheckBox::indicator:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {PURPLE2}, stop:1 {PURPLE});
                                border-color: {PURPLE2}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 1px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.10); border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {PURPLE}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 1px 4px; }}
QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.10); border-radius: 3px; min-width: 36px; }}
QProgressBar {{ background: rgba(255,255,255,0.05); border: none; border-radius: 5px; height: 10px; text-align: center;
                font-size: 8pt; }}
QProgressBar::chunk {{ border-radius: 5px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {PURPLE}, stop:1 {CYAN}); }}
QSlider::groove:horizontal {{ height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {PURPLE}, stop:1 {CYAN}); border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; margin: -6px 0; border-radius: 8px; border: 3px solid {PURPLE}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {PURPLE2}; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {GLASS_BORDER}; padding: 7px 9px; border-radius: 8px; }}
QListWidget {{ background: transparent; border: none; }}
QListWidget::item {{ padding: 6px; border-radius: 8px; }}
QListWidget::item:hover {{ background: rgba(255,255,255,0.04); }}
QTableWidget {{ background: {GLASS}; border: 1px solid {GLASS_BORDER}; border-radius: 12px; gridline-color: {BORDER}; }}
QHeaderView::section {{ background: rgba(255,255,255,0.04); border: none; padding: 7px; color: {MUTED}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {GLASS_BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: rgba(139,92,246,0.30); }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMessageBox {{ background: {SURFACE}; }}
"""

# 🎮 iGaming Platform — Setup Guide

A web-based iGaming platform with a dedicated support service. This repository contains two independent components that can be installed and launched separately.

## 📂 Project Structure

```text
📦 Project
 ├── 📁 fixed_out/         # Main iGaming website
 │   ├── main.py
 │   └── requirements.txt
 │
 └── 📁 velox_support/     # Support service
     ├── main.py
     └── requirements.txt
```

### 🧩 Components

| Directory | Description |
|---|---|
| `fixed_out` | Main iGaming website and core platform |
| `velox_support` | Dedicated support service |

---

## ⚙️ Installation

**1. Clone the repository**

```bash
git clone <repository-url>
cd <repository-folder>
```

**2. Install dependencies**

Each component has its own requirements. Install them separately.

**Main website — `fixed_out`**

```bash
cd fixed_out
pip install -r requirements.txt
```

**Support service — `velox_support`**

```bash
cd ../velox_support
pip install -r requirements.txt
```

> **Note:** Make sure Python and pip are installed before proceeding.

---

## 🚀 Getting Started

Both components use the same startup command:

```bash
python main.py
```

### 🌐 Main Website

```bash
cd fixed_out
python main.py
```

### 🛠️ Support Service

Open a separate terminal and run:

```bash
cd velox_support
python main.py
```

> **Important:** Run each component from its respective directory. Use separate terminal sessions if you want both services running simultaneously.

---

## ☕ Support the Developer

If you find this project useful and would like to support its development, a small donation would be greatly appreciated!

**TON Wallet (Keeper)**

```text
UQCUpab2tXKd5qEI05G7iuZIGsJ6klNl5ZCwTnCoHELmNoDk
```

</p>

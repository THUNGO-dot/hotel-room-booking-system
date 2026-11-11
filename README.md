# 🏨 HOTEL ROOM BOOKING SYSTEM

> **Course:** Software Engineering  
> **University:** University of Transport in Ho Chi Minh City (UTH)  
> **Group:** Group 07  

---

## 📌 Project Overview
The **Hotel Room Booking System** is an application designed to manage room booking processes, customer information, room status, and hotel revenue. The system optimizes operational workflows for receptionists/managers while providing a seamless booking experience for customers.

---

## 👥 Team Members & Task Assignment

| Name | Student ID | Task Assignment & Responsibilities | Completion |
| :--- | :---: | :--- | :---: |
| **Ngo Thi Anh Thu** | 087306008195 | **Team Leader** <br>• Section 6.2 (Logical Schema)<br>• Chapter 7 (Implementation & GUI)<br>• Backend & Database Integration | 100% |
| **Tran Do Thuy Vy** | 083306009260 | **Member** <br>• Chapter 3 (Process 3, Process 4)<br>• Chapter 4 (System Architecture)<br>• Business Logic & Documentation | 100% |
| **Dinh Tran Thanh Thuong** | 060307006633 | **Member** <br>• Chapter 1 (Introduction & Scope)<br>• Chapter 2 (Requirements Analysis)<br>• Chapter 3 (DFD Level 0) | 100% |
| **Tran Thi Kieu Trinh** | 066306009531 | **Member** <br>• Chapter 5 (Class Diagram & Design)<br>• Section 6.1 (Conceptual ERD)<br>• QA & Test Cases | 100% |
| **Nguyen Thai Bao Nguyen** | 052207007340 | **Member** <br>• Chapter 3 (DFD Level 1, Process 1, Process 2)<br>• Frontend & UI Integration | 100% |

---

## ✨ Key Features

- **Customer:**
  - Search available rooms by date, room type, and price range.
  - Book rooms online and receive confirmation.
  - View booking history.

- **Receptionist / Staff:**
  - Manage Check-in / Check-out for guests.
  - Update room statuses (Available, Booked, Cleaning, Maintenance).
  - Issue billing invoices.

- **Admin:**
  - Manage room categories, room types, and service pricing.
  - Manage staff accounts and role permissions.
  - Generate reports on revenue and room occupancy rates.

---

## 🛠️ Technologies Used

- **Programming Language:** Python 3.x
- **User Interface (GUI):** Python GUI (PyQt / CustomTkinter)
- **Database:** SQL Server / MySQL
- **Management & Design Tools:** Git/GitHub, Visual Studio Code, Draw.io, DBeaver

---

## 📂 Project Structure

```text
hotel-room-booking-system/
├── data/             # Sample datasets (CSV, JSON, SQL seed data)
├── docs/             # Design documents and course reports
│   ├── design/       # UI/UX design assets (Wireframes, Mockups)
│   ├── diagrams/     # UML diagrams (Use Case, DFD, ERD, Class Diagram)
│   ├── report/       # Report documents (Word/PDF)
│   ├── sql_script/   # Database initialization scripts (.sql)
│   └── test/         # Test scenarios and Test Cases
├── src/              # Main application source code (Python GUI & Logic)
│   ├── gui/          # PyQt / CustomTkinter interface components
│   └── main.py       # Application entry point
├── tests/            # Automated test code (Unit Tests)
├── .gitignore        # Files and folders ignored by Git
├── README.md         # Project documentation file
└── requirements.txt  # List of required Python dependencies 
```
---
 
 ## ⚙️ Installation & Run

1. Clone the repository:
   ```bash
   git clone https://github.com/THUNGO-dot/hotel-room-booking-system.git
   cd hotel-room-booking-system
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python src/main.py
   ```
import os
import json
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Elderly Care MCP")
DATA_FILE = "elderly_care_data.json"

def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {
            "medications": [
                {"name": "Aspirin", "dosage": "81mg", "schedule": "Once daily in the morning"},
                {"name": "Metformin", "dosage": "500mg", "schedule": "Twice daily with meals"}
            ],
            "medication_logs": [],
            "appointments": [
                {"doctor": "Dr. Davis", "specialty": "Cardiologist", "date": "2026-07-15", "time": "10:00 AM", "location": "Heart Clinic Suite 300", "status": "Confirmed"}
            ]
        }
    with open(DATA_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"medications": [], "medication_logs": [], "appointments": []}

def save_data(data: Dict[str, Any]):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

@mcp.tool()
def list_medications() -> str:
    """List all configured medications and their schedules.
    
    Returns:
        A JSON-formatted string listing medications.
    """
    data = load_data()
    return json.dumps(data.get("medications", []), indent=2)

@mcp.tool()
def add_medication(name: str, dosage: str, schedule: str) -> str:
    """Add a new medication and its schedule to the tracking list.
    
    Args:
        name: Name of the medication.
        dosage: Dosage of the medication (e.g., 50mg, 1 tablet).
        schedule: How often and when it should be taken.
        
    Returns:
        Confirmation message.
    """
    data = load_data()
    new_med = {"name": name, "dosage": dosage, "schedule": schedule}
    data["medications"].append(new_med)
    save_data(data)
    return f"Successfully added medication: {name} ({dosage}), schedule: {schedule}."

@mcp.tool()
def log_medication_taken(name: str, time_taken: str) -> str:
    """Log that a medication was taken at a specific time.
    
    Args:
        name: Name of the medication taken.
        time_taken: Time or description of when it was taken (e.g., '8:30 AM', 'just now').
        
    Returns:
        Confirmation message.
    """
    data = load_data()
    log_entry = {"name": name, "time_taken": time_taken}
    data["medication_logs"].append(log_entry)
    save_data(data)
    return f"Logged that {name} was taken at {time_taken}."

@mcp.tool()
def list_appointments() -> str:
    """List all scheduled doctor visits and appointments.
    
    Returns:
        A JSON-formatted string listing appointments.
    """
    data = load_data()
    return json.dumps(data.get("appointments", []), indent=2)

@mcp.tool()
def schedule_doctor_visit(doctor: str, specialty: str, date: str, time: str, location: str) -> str:
    """Schedule a pending doctor visit appointment.
    
    Args:
        doctor: Name of the doctor.
        specialty: Medical specialty of the doctor.
        date: Date of the appointment (YYYY-MM-DD or relative description).
        time: Time of the appointment.
        location: Clinic address or location.
        
    Returns:
        A confirmation message indicating the appointment is created with 'Pending' status.
    """
    data = load_data()
    new_app = {
        "doctor": doctor,
        "specialty": specialty,
        "date": date,
        "time": time,
        "location": location,
        "status": "Pending"
    }
    data["appointments"].append(new_app)
    save_data(data)
    return f"Doctor visit with {doctor} ({specialty}) on {date} at {time} scheduled as PENDING. Needs caregiver confirmation."

@mcp.tool()
def confirm_pending_appointment(doctor: str, date: str) -> str:
    """Confirm a pending doctor visit appointment.
    
    Args:
        doctor: Name of the doctor.
        date: Date of the appointment.
        
    Returns:
        A status message.
    """
    data = load_data()
    for app in data.get("appointments", []):
        if app["doctor"].lower() == doctor.lower() and app["date"] == date and app["status"] == "Pending":
            app["status"] = "Confirmed"
            save_data(data)
            return f"Appointment with {doctor} on {date} has been CONFIRMED."
    return f"No pending appointment found with {doctor} on {date}."

if __name__ == "__main__":
    mcp.run()

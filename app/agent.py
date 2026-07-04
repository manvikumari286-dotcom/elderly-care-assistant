import os
import sys
import re
import datetime
import json
from typing import Optional, List, Dict, Any, Generator

from google.adk.workflow import Workflow, START, node, FunctionNode
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

from app.config import config

# --- Security Audit Logging ---
def audit_log(decision: str, details: dict, severity: str = "INFO"):
    log_entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "component": "SecurityCheckpoint",
        "decision": decision,
        "severity": severity,
        "details": details
    }
    print(f"AUDIT_LOG: {json.dumps(log_entry)}")

# --- Security Checkpoint Node ---
def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    """Security node to check prompt injections and scrub PII."""
    raw_prompt = ""
    if isinstance(node_input, types.Content):
        if node_input.parts:
            raw_prompt = " ".join([p.text for p in node_input.parts if p.text])
    elif isinstance(node_input, str):
        raw_prompt = node_input
    
    # 1. PII Scrubbing (names, phone numbers, SSNs)
    phone_pattern = re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b')
    scrubbed_prompt = phone_pattern.sub("[REDACTED_PHONE]", raw_prompt)
    
    ssn_pattern = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
    scrubbed_prompt = ssn_pattern.sub("[REDACTED_ID]", scrubbed_prompt)

    # 2. Prompt Injection Check
    injection_keywords = ["ignore previous instructions", "system prompt", "you are now", "jailbreak", "bypass"]
    has_injection = any(kw in raw_prompt.lower() for kw in injection_keywords)
    
    # 3. Domain-specific rule: Medical Emergency Check
    emergency_keywords = ["chest pain", "bleeding heavily", "suicidal", "emergency", "dying", "heart attack"]
    is_emergency = any(kw in raw_prompt.lower() for kw in emergency_keywords)

    if has_injection:
        audit_log("prompt_injection_detected", {"raw_prompt": raw_prompt}, "CRITICAL")
        return Event(
            output="Security violation: Unauthorized system instruction bypass attempt detected.",
            route="security_violation"
        )

    if is_emergency:
        audit_log("medical_emergency_detected", {"raw_prompt": raw_prompt}, "WARNING")
        return Event(
            output="EMERGENCY ALERT: If you are experiencing a life-threatening medical emergency (like chest pain, heavy bleeding, or severe difficulty breathing), please call 911 immediately or go to the nearest emergency room. We cannot schedule emergency medical help.",
            route="security_violation"
        )

    audit_log("request_allowed", {"scrubbed_prompt": scrubbed_prompt}, "INFO")
    ctx.state["scrubbed_prompt"] = scrubbed_prompt
    return Event(output=scrubbed_prompt, route="safe")

def security_violation_handler(node_input: str):
    """Handles security warning output."""
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=node_input)]))
    yield Event()

# --- Sub-agents and Orchestrator Configuration ---
gemini_model = Gemini(
    model=config.model,
    retry_options=types.HttpRetryOptions(attempts=3),
)

# Start MCP Server via Stdio Connection.
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["app/mcp_server.py"],
        )
    )
)

medication_agent = LlmAgent(
    name="medication_agent",
    model=gemini_model,
    instruction="""You are a specialized Medication Assistant for elderly care.
You help track medication schedules, list active medications, log when medication is taken, and provide general safety instructions about dosages.
You must use the available tools (list_medications, add_medication, log_medication_taken) to retrieve and update the medication records.
Always be warm, gentle, and precise, highlighting medication times and dosages. Keep your responses clear and easy to read.
Use markdown tables or bullet points for lists of medications to make it very clear for elderly readers.

If a user asks about symptoms like fever or pain, or requests specific dosage guidance:
1. Provide helpful and safe home care suggestions (such as resting, staying well-hydrated with water/clear fluids, keeping the room at a comfortable cool temperature, and using a cool damp washcloth on the forehead).
2. For over-the-counter fever reducers/pain relievers (e.g., Acetaminophen/Tylenol or Ibuprofen), provide general safety information (e.g., emphasizing the importance of reading and following the package label, checking for active ingredients to avoid accidental double-dosing, and not exceeding daily limits) while advising them to confirm with their doctor or pharmacist for their specific health history.
3. Warmly remind them to contact a doctor or seek medical care if the fever is high (e.g., above 103°F/39.4°C), if symptoms persist for more than 3 days, or if they notice worsening signs.""",
    description="Handles all medication tracking, logging, and general medication queries.",
    tools=[mcp_toolset]
)

appointment_agent = LlmAgent(
    name="appointment_agent",
    model=gemini_model,
    instruction="""You are a specialized Doctor Visit Coordinator for elderly care.
You help coordinate doctor appointments, list upcoming visits, and note doctor specialties and locations.
You must use the available tools (list_appointments, schedule_doctor_visit) to retrieve and update doctor visit records.
When scheduling a doctor visit, make sure you collect:
- Doctor name
- Specialty
- Date
- Time
- Location
Once you have collected these, call the `schedule_doctor_visit` tool to save it as a pending appointment.
Always be organized and professional. Let the user know the visit has been scheduled as pending and needs caregiver confirmation.""",
    description="Coordinates, schedules, and queries doctor appointments.",
    tools=[mcp_toolset]
)

orchestrator = LlmAgent(
    name="orchestrator",
    model=gemini_model,
    instruction="""You are the main coordinator for the Elderly Care Assistant.
Your goal is to help users manage medication schedules and coordinate doctor visits.
You have access to specialized sub-agents:
- Always delegate any queries about medications, symptoms (such as fever or pain), dosages, instructions, or home care remedies to the medication_agent.
- Use the appointment_agent to schedule, retrieve, or update doctor visits.
 
If the user wants to add/schedule a doctor visit, delegate it to the appointment_agent, which will call the scheduling tool.
Keep your tone warm, respectful, and clear, suitable for elderly users or their caregivers.""",
    tools=[AgentTool(medication_agent), AgentTool(appointment_agent)],
    description="Coordinates all requests for medication and doctor appointments."
)

# --- Post-Orchestrator Routing Node ---
def check_pending_appointments() -> Optional[dict]:
    if os.path.exists("elderly_care_data.json"):
        try:
            with open("elderly_care_data.json", "r") as f:
                data = json.load(f)
                for app in data.get("appointments", []):
                    if app.get("status") == "Pending":
                        return app
        except Exception:
            pass
    return None

def route_post_orchestrator(ctx: Context, node_input: Any) -> Event:
    """Inspects if there are any pending appointments that require caregiver confirmation."""
    pending = check_pending_appointments()
    if pending:
        ctx.state["pending_appointment_details"] = pending
        return Event(output=node_input, route="needs_confirmation")
    return Event(route="direct_output")

# --- Caregiver Confirmation (HITL) Node ---
def appointment_confirmation(ctx: Context, node_input: Any) -> Generator[Any, None, None]:
    """Handles Human-in-the-Loop caregiver verification for pending doctor visits."""
    pending = ctx.state.get("pending_appointment_details")
    if not pending:
        yield Event(output="No pending appointment found to confirm.", route="no_pending")
        return

    # Check if we have received a response
    if not ctx.resume_inputs or "confirm_appointment" not in ctx.resume_inputs:
        msg = f"Caregiver Verification Required: Please confirm scheduling the doctor visit with {pending['doctor']} ({pending['specialty']}) on {pending['date']} at {pending['time']}? (Reply 'Yes' to confirm or 'No' to cancel)"
        yield RequestInput(interrupt_id="confirm_appointment", message=msg)
        return

    # Process caregiver response
    choice = ctx.resume_inputs.get("confirm_appointment", "").lower().strip()
    
    if os.path.exists("elderly_care_data.json"):
        try:
            with open("elderly_care_data.json", "r") as f:
                data = json.load(f)
            
            found = False
            for app in data.get("appointments", []):
                if (app["doctor"].lower() == pending["doctor"].lower() and 
                        app["date"] == pending["date"] and 
                        app["status"] == "Pending"):
                    if "yes" in choice:
                        app["status"] = "Confirmed"
                    else:
                        app["status"] = "Cancelled"
                    found = True
                    break
            
            if found:
                with open("elderly_care_data.json", "w") as f:
                    json.dump(data, f, indent=2)
                
                if "yes" in choice:
                    output_text = f"Doctor visit with {pending['doctor']} on {pending['date']} at {pending['time']} is now CONFIRMED."
                else:
                    output_text = f"Scheduling for doctor visit with {pending['doctor']} has been CANCELLED."
            else:
                output_text = "Pending appointment details could not be found."
        except Exception as e:
            output_text = f"Error updating appointment status: {str(e)}"
    else:
        output_text = "Database file missing."

    # Clear pending details from state
    ctx.state["pending_appointment_details"] = None
    
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=output_text)]))
    yield Event()

# --- Final Output Node ---
# --- Workflow Definition ---
root_agent = Workflow(
    name="elderly_care_assistant_workflow",
    edges=[
        ('START', security_checkpoint),
        (security_checkpoint, {
            "security_violation": security_violation_handler,
            "safe": orchestrator
        }),
        (orchestrator, route_post_orchestrator),
        (route_post_orchestrator, {
            "needs_confirmation": appointment_confirmation,
        })
    ]
)


app = App(
    root_agent=root_agent,
    name="app",
)

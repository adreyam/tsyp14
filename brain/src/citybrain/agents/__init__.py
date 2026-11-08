"""
Registry of the four axis agents. The agent code lives with its axis:

    brain/axes/emergency/agent/        brain/axes/transportation/agent/
    brain/axes/pollution/agent/        brain/axes/energy/agent/

base.py (here) is the pattern they all follow: detect -> decide -> act -> explain.
"""
from axes.emergency.agent import EmergencyAgent
from axes.energy.agent import EnergyAgent
from axes.pollution.agent import PollutionAgent
from axes.transportation.agent import TransportationAgent

AGENT_CLASSES = {"emergency": EmergencyAgent, "transportation": TransportationAgent,
                 "pollution": PollutionAgent, "energy": EnergyAgent}

__all__ = ["AGENT_CLASSES", "EmergencyAgent", "TransportationAgent", "PollutionAgent", "EnergyAgent"]

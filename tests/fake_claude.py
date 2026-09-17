#!/usr/bin/env python3
"""Stands in for the Claude Code program in tests, so no real plan usage is spent.

`auth status` reports signed in. `-p` reads the prompt and answers with a
tailoring plan for tests/sample_profile.json that mixes honest rewording with
invented claims, which the fact checks must catch.
"""

import json
import sys

args = sys.argv[1:]

if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    sys.exit(0)

if "-p" in args:
    prompt = sys.stdin.read()
    if "JOB POSTING" not in prompt or "STUDENT PROFILE" not in prompt:
        print(json.dumps({"is_error": True, "result": "prompt missing job or profile"}))
        sys.exit(1)
    plan = {
        "coursework": ["CS 330 (Computer Architecture)", "CS 210 (Data Structures)", "Quantum Basket Theory"],
        "high_school_honors": ["Robotics Club Captain"],
        "sections": [
            {
                "heading": "PROJECTS",
                "entries": [
                    {
                        "id": "weather-station",
                        "bullets": [
                            "Built a Raspberry Pi weather station that logs three sensors every 5 minutes with Python.",
                            "Trained a machine learning model to forecast rain from the station's data.",
                        ],
                    }
                ],
            },
            {
                "heading": "WORK EXPERIENCE",
                "entries": [
                    {
                        "id": "campus-help-desk",
                        "bullets": [
                            "Troubleshoot hardware and software issues, resolving about 40 tickets per week.",
                            "Managed a team of 12 technicians across campus.",
                        ],
                    },
                    {"id": "invented-internship", "bullets": ["Interned at a large tech company."]},
                ],
            },
        ],
        "skills": ["Raspberry Pi", "Python", "Kubernetes"],
        "changes": "Led with the embedded weather station project.",
    }
    envelope = {"type": "result", "is_error": False, "result": "```json\n" + json.dumps(plan) + "\n```"}
    print(json.dumps(envelope))
    sys.exit(0)

print(json.dumps({"is_error": True, "result": "unsupported call"}))
sys.exit(1)

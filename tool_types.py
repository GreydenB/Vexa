tools = [
    {
        "type": "function",
        "name": "open_application",
        "description": "Open Stated Application.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name of the application to open"
                }
            },
            "required": ["app_name"],
            "additionalProperties": False
        }
    }
]
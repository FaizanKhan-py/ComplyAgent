"""The autopilot: event-driven agents that run in the background, with no button to press.

    store.py    the task queue (agent_tasks) and each customer's saved context (customer_context)
    agents.py   what each task does: Analysis, Decision, Conversation and escalation
    runner.py   the workers that take tasks, the watchers that turn events into tasks, and the
                Monitor Agent that keeps an eye on the whole system

Everything lives in the database, so closing the browser, signing out or restarting the server
never loses work: a restarted server picks up exactly where it stopped.
"""

#!/usr/bin/env python3
"""Finds the relevant corpus notes for the Messenger coach."""





from redline.model import entrypoint
from redline.model.knowledge_search import main

if __name__ == "__main__":
    entrypoint.run(main)

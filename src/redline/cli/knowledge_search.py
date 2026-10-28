#!/usr/bin/env python3
"""Repère les fiches pertinentes du corpus pour le coach Messenger."""





from redline.model import entrypoint
from redline.model.knowledge_search import main

if __name__ == "__main__":
    entrypoint.run(main)

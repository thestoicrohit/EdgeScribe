import os

# Must be set before edgescribe.config is imported: tests never touch the real database.
os.environ["EDGESCRIBE_DB"] = ":memory:"

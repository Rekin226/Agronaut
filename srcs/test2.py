"""
import ollama

response = ollama.chat("mistral", messages=[
    {"role": "system", "content": "Aquaponics is a food production system that couples aquaculture (raising aquatic animals such as fish, crayfish, snails or prawns in tanks) with hydroponics (cultivating plants in water) whereby the nutrient-rich aquaculture water is fed to hydroponically grown plants."},
    {"role": "user", "content": "What is aquaponics?"}
])

print(response["message"]["content"])
"""

import ollama

response = ollama.chat("llama2", messages=[
    {"role": "system", "content": "Aquaponics is a food production system that couples aquaculture (raising aquatic animals such as fish, crayfish, snails or prawns in tanks) with hydroponics (cultivating plants in water) whereby the nutrient-rich aquaculture water is fed to hydroponically grown plants."},
    {"role": "user", "content": "What is aquaponics?"}
])

print(response["message"]["content"])

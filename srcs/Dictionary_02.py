# -*- coding: utf-8 -*-
"""
Created on Tue Nov 26 12:25:24 2024

@author: User
"""

import pandas as pd
from bs4 import BeautifulSoup
import requests
import beautifulsoup as bs

df = pd.DataFrame({
    "topic": ["Water Quality Control", "Water Quality Control", "Fish Farming", "Plant Nutrition", "Plant Nutrition"],
    "subtopic": [
        "Smart Aquaponics Water Quality Monitoring and Management", 
        "Challenges in Water Quality for Aquaponics Systems", 
        "Fish Behavior Monitoring in Aquaponics Systems", 
        "Optimization of Plant Nutrition in Aquaponics", 
        "Challenges in Plant Growth within Aquaponics"
        ],
    "content": [
        "Exploring real-time monitoring and management of water quality parameters using IoT technology in smart aquaponics systems to improve efficiency and productivity.",
        "Discussing the challenges of maintaining stable water quality in commercial aquaponics systems and potential solutions.",
        "Utilizing YOLO v4 deep learning algorithm for image-based monitoring of fish activity in aquaponics systems to assess their health status.",
        "Researching the use of artificial intelligence to optimize nutrient supply in aquaponics systems to enhance plant growth efficiency.",
        "Exploring challenges in plant cultivation in aquaponics, such as nutrient deficiencies, and proposing possible solutions."
        ],
    "tags": [
        "IoT, smart farming, water quality monitoring", 
        "Water quality management, commercialization, sustainability", 
        "Fish behavior, deep learning, image processing", 
        "Artificial intelligence, plant nutrition, system optimization", 
        "Plant cultivation, nutrient supply, system challenges"
        ],
    "source": [
        "https://doi.org/10.1007/S10462-024-11003-X", 
        "https://doi.org/10.3390/SU7044199", 
        "https://doi.org/10.1016/J.COMPAG.2022.106785", 
        "https://doi.org/10.1109/ICMI60790.2024.10586162", 
        "https://doi.org/10.3390/SU7044199"
        ]
})

# read html
html_path = df["source"].values[0]
#print('The path for the html is: ', html_path)


# Extract text from a URL
url = html_path
response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')
text = soup.get_text()
#print(text)

# Create a function to extract text from a URLˆ
def extract_text_from_url(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    text = soup.get_text()
    return text















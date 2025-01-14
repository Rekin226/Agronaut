# -*- coding: utf-8 -*-
"""
Created on Tue Nov 26 12:25:24 2024

@author: User
"""

import pandas as pd
from bs4 import BeautifulSoup
import requests

df = pd.DataFrame({
    "topic": ["Plant Care"],
    "subtopic": ["Plant nutrients"],
    "content": ["To improve photosynthesis, nutrient levels, and growth while identifying system limitations."],
    "tags": ["gas exchange, light response curves"],
    "source": ["https://dx.doi.org/10.3390/horticulturae9030291"]
})

# read html
html_path = df["source"].values[0]
print('The path for the html is: ', html_path)


# Extract text from a URL
url = html_path
response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')
text = soup.get_text()
print(text)

# Create a function to extract text from a URLˆ
def extract_text_from_url(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    text = soup.get_text()
    return text













#df = pd.DataFrame({
#    "topic": ["Fish Nutrition", "Plant Nutrition", "Water Quality"],
#    "subtopic": ["Fish nutrients", "Plant growth", "Water pH"],
#    "content": [
#        "Potassium, iron, copper accumulation details.",
#        "Nutrient absorption and photosynthesis.",
#        "Optimal pH range for aquaculture."
#    ],
#    "tags": ["Fish health", "Plant care", "Water quality"]
#})

#data = {
#    "topic": "Fish Nutrition",
#    "subtopic": "Fish feed",
#   "content": "Using outputs for biogas and fertilizer in aquaponics with added nutrients is feasible.",
#    "tags": "Fish feed,Fish health"
#}

#new_data = pd.DataFrame([data])
#df = pd.concat([df, new_data], ignore_index=True)
#tagged_data = df[df["tags"].str.contains("Fish feed")]
#print(tagged_data)

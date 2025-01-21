# -*- coding: utf-8 -*-
"""
Created on Tue Nov 26 12:25:24 2024

@author: User
"""

import pandas as pd
from bs4 import BeautifulSoup
import requests
<<<<<<< HEAD
#import beautifulsoup as bs
=======
>>>>>>> 2adb459f174d42c8449985a064807b2f2e3c4451

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















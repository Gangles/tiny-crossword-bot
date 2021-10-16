#!/usr/bin/python
import json

def initBlacklist():
    with open("blacklist.json") as json_file:
        json_data = json.load(json_file)
        return json_data['badwords']

def isOffensive(word):
    global blacklist
    if not word:
        return False
    for badword in blacklist:
        if word.lower().find(badword) >= 0:
            print(badword + " is offensive")
            return True
    return False
    
blacklist = initBlacklist()
#!/usr/bin/python
# -*- coding: utf-8 -*-

import blacklist
import config
import copy
import datetime
import logging
import math
import os
import psycopg2
import pytz
import random
import re
import string
import sys
import time
import urlparse
import wikipedia
from PIL import Image
from text_unidecode import unidecode
from twython import Twython
from wikipedia.exceptions import DisambiguationError, HTTPTimeoutError

def db_connect():
	# connect to the postgres database
	urlparse.uses_netloc.append("postgres")
	url = urlparse.urlparse(os.environ["DATABASE_URL"])
	return psycopg2.connect(
	    database = url.path[1:],
	    user = url.username,
	    password = url.password,
	    host = url.hostname,
	    port = url.port
	)

def db_verify_connection(postgres):
	assert postgres, "No database connection"
	assert not postgres.closed, "Connection unexpectedly closed"

def db_init(postgres):
	# make sure the db table exists
	db_verify_connection(postgres)
	cur = postgres.cursor()
	cur.execute('CREATE TABLE IF NOT EXISTS puzzles ('
				'tweet_id varchar(30) PRIMARY KEY, '
				'topic_1 varchar(40) NOT NULL, '
				'topic_2 varchar(40) NOT NULL, '
				'topic_3 varchar(40) NOT NULL, '
				'matrix varchar(1000) NOT NULL);')
	cur.close()

def db_query(postgres):
	# check if a solution is waiting to be tweeted
	db_verify_connection(postgres)
	cur = postgres.cursor()
	cur.execute('SELECT * FROM puzzles;')
	row = cur.fetchone()
	cur.close()
	return row or (None, None, None, None, None)

def db_insert(postgres, tweet_id, crossword_hints, matrix):
	# record the puzzle and solution
	db_verify_connection(postgres)
	t1 = crossword_hints[0]['topic']
	t2 = crossword_hints[1]['topic']
	t3 = crossword_hints[2]['topic']
	solution = matrix_to_string(matrix)
	cur = postgres.cursor()
	cur.execute('INSERT INTO puzzles (tweet_id, topic_1, topic_2, topic_3, matrix)'
		' VALUES (%s, %s, %s, %s, %s);', (tweet_id, t1, t2, t3, solution))
	postgres.commit()
	cur.close()

def db_clear(postgres):
	# clear the DB table completely
	db_verify_connection(postgres)
	cur = postgres.cursor()
	cur.execute('TRUNCATE puzzles;')
	postgres.commit()
	cur.close()

def print_safe(to_print):
	# heroku doesn't like unicode
	try:
		print unidecode(to_print)
	except UnicodeDecodeError as e:
		logging.exception("Unicode Decode error")

def substring_after(s, delims):
	# get the longest substring after the given delim
	part = ''
	for delim in delims:
		new_part = s.partition(delim)[2]
		if new_part and len(new_part) > len(part):
			part = new_part
	return part

def get_summary(topic, attempts=0):
	try:
		return wikipedia.summary(topic, sentences=2)
	except DisambiguationError as e:
		return ""
	except HTTPTimeoutError as e:
		return ""

def get_new_words(crossword_hints):
	remove_brackets = re.compile(r' \([^)]*\)')
	remove_topic_punc = re.compile(r'^([^,\(]*)')
	remove_hint_punc = re.compile(r'^([^.;:!?\(]*)')

	try:
		print_safe("Getting random topics from Wikipedia...")
		random_topics = wikipedia.random(pages=10)
	except HTTPTimeoutError as e:
		random_topics = []

	for wiki_topic in random_topics:
		# reject some vague topics right away
		topic_lower = wiki_topic.lower()
		if ' in ' in topic_lower: continue
		if 'list of ' in topic_lower: continue
		if 'iowa' in topic_lower: continue # too many
		if blacklist.isOffensive(wiki_topic): continue

		# clean up the topic string
		topic = re.sub(remove_brackets, '', wiki_topic)
		topic = re.search(remove_topic_punc, topic).group(0)
		topic = topic.strip()

		# reject too short or too long
		if len(topic) > 20 or len(topic) < 3 or len(topic.split()) > 4:
			continue
		
		time.sleep(1) # be kind to wikipedia's servers
		print_safe("Getting summary for " + wiki_topic + "...")
		summary = get_summary(wiki_topic)

		if summary:
			# avoid disambiguation blurbs
			if 'this article is about' in summary.lower(): continue

			# get the description of the topic
			hint = substring_after(summary, [' is ', ' was ', ' are ', ' were '])

			# reject hints that are too vague to solve
			hint_lower = hint.lower()
			if ' commune ' in hint_lower: continue
			if ' parish ' in hint_lower: continue
			if ' district ' in hint_lower: continue
			if ' town ' in hint_lower: continue
			if ' rayon of ' in hint_lower: continue
			if ' municipality in ' in hint_lower: continue
			if ' county ' in hint_lower: continue
			if ' actor' in hint_lower: continue
			if ' actress' in hint_lower: continue
			if ' singer' in hint_lower: continue
			if 'football player' in hint_lower: continue
			if 'footballer' in hint_lower: continue
			if 'figure skater' in hint_lower: continue
			if 'racing driver' in hint_lower: continue
			if 'soccer player' in hint_lower: continue
			if 'basketball player' in hint_lower: continue
			if 'baseball player' in hint_lower: continue
			if 'tennis player' in hint_lower: continue
			if 'cricketer' in hint_lower: continue
			if 'politician' in hint_lower: continue
			if 'common year' in hint_lower: continue
			if ' a year in ' in hint_lower: continue
			if ' a list of ' in hint_lower: continue
			if blacklist.isOffensive(hint): continue

			# clean up the hint string
			hint = re.sub(r'U\.S\.', 'US', hint)
			hint = re.sub(remove_brackets, '', hint)
			hint = re.search(remove_hint_punc, hint).group(0)
			hint = hint.strip()

			if len(hint) < 5 or len(hint) > 36 or len(hint.split()) < 4:
				continue # too long or too short
			else:
				new_topic = {'topic': topic, 'hint': hint,
					'crossword': get_crossword_string(topic)}
				crossword_hints.append(new_topic)
	return crossword_hints

def get_crossword_string(topic):
	# return upper case alphanumeric only
	topic = unidecode(topic)
	topic = re.sub('&', 'AND', topic)
	topic = topic.upper()
	only_alphanumeric = re.compile('[\W]+')
	return only_alphanumeric.sub('', topic)

def validate_crossword(middle, first, second):
	# reject short middle words
	if len(middle) < 6:
		return (False, None, None)
	
	# split the horizontal word in half
	half_index = int(math.floor(len(middle) / 2.0)) - 1
	first_half = middle[1:half_index]
	second_half = middle[(half_index + 2):]
	first_set, second_set = set(first), set(second)

	# check for common letters with the two vertical words
	first_intersect = set(first_half).intersection(first_set)
	second_intersect = set(second_half).intersection(second_set)

	if len(first_intersect) > 0 and len(second_intersect) > 0:
		return (True, first_intersect, second_intersect)
	else:
		return (False, None, None)

def find_letter_index(string, letter_set):
	# find the first instance of the given set in the string
	for i, letter in enumerate(string):
		if letter in letter_set:
			return (i, letter)
	raise Exception("Can't find letter from set")

def write_column(matrix, solved, upper_row, column, string, symbol):
	# write one of the vertical words down a column
	for row in range(upper_row, upper_row + len(string)):
		if not '1' in matrix[row][column]:
			matrix[row][column] = (row > upper_row) and '@' or symbol
			solved[row][column] = string[row - upper_row]

def get_puzzle_matrix(crossword_hints):
	# validate that the crossword can be arranged
	middle = crossword_hints[0]['crossword']
	first  = crossword_hints[1]['crossword']
	second = crossword_hints[2]['crossword']
	is_valid, first_set, second_set = validate_crossword(middle, first, second)

	# create a 2D list with the letter arrangement
	if is_valid:
		# find the index of matching letters
		first_index, first_match = find_letter_index(first, first_set)
		second_index, second_match = find_letter_index(second, second_set)

		# determine the dimensions of the puzzle grid
		space_above = max(first_index, second_index)
		space_below = max(len(first) - first_index, len(second) - second_index)
		height = space_above + space_below
		width = len(middle)

		# 2d matrices for the puzzle and the solution
		matrix = [['.' for x in xrange(width)] for x in xrange(height)]
		solved = copy.deepcopy(matrix)

		row = space_above
		for column in range(width):
			# write the horizontal word in both matrices
			matrix[row][column] = (column > 0) and '@' or '1'
			solved[row][column] = middle[column]

			# check if it's time to write one of the vertical words
			if first_match and first_match in middle[column] and column < (width / 2) and column > 0:
				write_column(matrix, solved, row - first_index, column, first, '2')
				first_match = None
			elif second_match and second_match in middle[column] and column > (width / 2):
				write_column(matrix, solved, row - second_index, column, second, '3')
				second_match = None

		assert not first_match, "Failed to write first vertical word."
		assert not second_match, "Failed to write second vertical word."
		
		return (matrix, solved, width, height)
	else:
		return (None, None, 0, 0)

def matrix_to_string(matrix):
	# convert the 2D matrix into one long string
	tostring = ""
	for row in xrange(len(matrix)):
		tostring += "".join(matrix[row]) + "\n"
	return tostring

def make_puzzle_image(matrix, name, solution=False):
	# make an image file from the given 2D matrix
	width, height = len(matrix[0]), len(matrix) 
	tiles = {}
	tiles['.'] = Image.open('./tiles/black.gif')
	if solution:
		# solution tileset
		for letter in string.ascii_uppercase:
			tiles[letter] = Image.open('./tiles/letter_' + letter + '.gif')
		for digit in string.digits:
			tiles[digit] = Image.open('./tiles/letter_' + digit  + '.gif')
	else:
		# puzzle tileset
		tiles['@'] = Image.open('./tiles/blank.gif')
		tiles['1'] = Image.open('./tiles/blank_1.gif')
		tiles['2'] = Image.open('./tiles/blank_2.gif')
		tiles['3'] = Image.open('./tiles/blank_3.gif')

	puzzle_image = Image.new('RGB', (width * 60, height * 60))

	for column in xrange(width):
		pos_x, pos_y = column * 60, 0
		for row in xrange(height):
			symbol = matrix[row][column]
			puzzle_image.paste(tiles[symbol], (pos_x, pos_y))
			pos_y += 60

	puzzle_image.save(name)
	return name

def connect_twitter():
    # connect to twitter API
    return Twython(config.twitter_key, config.twitter_secret,
    			config.access_token, config.access_secret)

def post_tweet(twitter, to_tweet, image_name, reply_to=None):
	# post the string and the image to twitter
	print_safe(to_tweet)
	puzzle_image = open(image_name, 'rb')
	return twitter.update_status_with_media(status=to_tweet,
		media=puzzle_image, in_reply_to_status_id=reply_to)

def post_new_puzzle(postgres):
	# find three hints for the crossword
	wikipedia.set_lang('simple')
	wikipedia.set_rate_limiting(True)
	crossword_hints = []
	attempts = 0
	while len(crossword_hints) < 3:
		assert attempts < 30, "Too many attempts"
		crossword_hints = get_new_words(crossword_hints)
		attempts += 1
	
	# sort the words, longest first
	crossword_hints = sorted(crossword_hints, key=lambda x:len(x['crossword']))
	crossword_hints.reverse()

	for word in crossword_hints:
		print_safe(word['topic'] + " / " + word['crossword'])
		print_safe(word['hint'] + "\n")

	matrix, solved = None, None
	while not matrix:
		# try to find a valid crossword
		matrix, solved, width, height = get_puzzle_matrix(crossword_hints)

		# if not, add words and random shuffle
		if not matrix:
			print_safe("Can't make crossword, retrying...")
			if random.random() < 0.33:
				assert attempts < 30, "Too many attempts"
				crossword_hints = get_new_words(crossword_hints)
				attempts += 1
			random.shuffle(crossword_hints)

	print_safe(matrix_to_string(solved))

	# make an image out of the matrix
	image_name = make_puzzle_image(matrix, 'puzzle.gif')

	# tweet the image and hints
	to_tweet =  "1: " + crossword_hints[0]['hint'] + "\n"
	to_tweet += "2: " + crossword_hints[1]['hint'] + "\n"
	to_tweet += "3: " + crossword_hints[2]['hint']
	twitter = connect_twitter()
	response = post_tweet(twitter, to_tweet, image_name)
	assert response['id'], "Failed posting puzzle to Twitter"

	# store the puzzle in the database
	db_insert(postgres, response['id'], crossword_hints, solved)

def correct_reply_count(reply, topics):
	# how many answers are in the given reply?
	crossword_reply = get_crossword_string(reply)
	count = 0
	for topic in topics:
		if get_crossword_string(topic) in crossword_reply:
			count += 1
	return count

def get_correct_answer(twitter, tweet_id, topics):
	# count the correct answers in each @-mention
	replies = twitter.get_mentions_timeline(count=200, since_id=tweet_id)
	replies.reverse() # oldest first
	best = {'name' : None, 'count' : 0}
	for reply in replies:
		correct_count = correct_reply_count(reply['text'], topics)
		if correct_count > best['count']:
			best['name'] = reply['user']['screen_name']
			best['count'] = correct_count
			# break early if 3/3 answers found
			if best['count'] > 2:
				break
	# return the highest number of correct answers
	return best

def post_solution(solution):
	print_safe("Posting puzzle solution")

	# assemble the tweet string
	tweet_id, t1, t2, t3, matrix_string = solution
	to_tweet = "SOLUTION\n"
	to_tweet += "1: " + t1 + "\n"
	to_tweet += "2: " + t2 + "\n"
	to_tweet += "3: " + t3

	# credit anyone who replied with correct answers
	twitter = connect_twitter()
	answer = get_correct_answer(twitter, tweet_id, [t1, t2, t3])
	if answer and answer['name'] and answer['count'] > 2:
		# someone solved all three
		to_tweet += "\nFirst correct answer by @" + answer['name']
		to_tweet += u" \U0001F389" # party popper
	elif answer and answer['name'] and answer['count'] > 0:
		# someone provided a partial answer
		to_tweet += "\n@" + answer['name']
		to_tweet += " had a partial solution [" + str(answer['count']) + "/3]"
		to_tweet += u" \U0001F31F" # glowing star

	# assemble an image with the solution
	matrix = matrix_string.rstrip().split('\n')
	image_name = make_puzzle_image(matrix, 'solution.gif', True)

	# post the solution to twitter as a reply
	response = post_tweet(twitter, to_tweet, image_name, tweet_id)
	assert response['id'], "Failed posting solution to Twitter"

def waitToTweet(hour):
	# tweet at the given hour in pacific time
	now = datetime.datetime.now(pytz.timezone('US/Pacific'))
	wait = 60 - now.second
	wait += (59 - now.minute) * 60
	if now.hour < hour:
		wait += (hour - 1 - now.hour) * 60 * 60
	else:
		wait += (hour + 23 - now.hour) * 60 * 60
	print_safe("Wait " + str(wait) + " seconds for next tweet")
	time.sleep(wait)

if __name__ == "__main__":
	# initialize database
	postgres = db_connect()
	db_init(postgres)
	
	while True:
		try:
			solution = db_query(postgres)
			if None in solution:
				# wait to post a new puzzle
				waitToTweet(12) # noon
				db_verify_connection(postgres)
				post_new_puzzle(postgres)
			else:
				# wait to post a solution
				waitToTweet(14) # 2pm
				db_verify_connection(postgres)
				post_solution(solution)
				db_clear(postgres)
		except:
			logging.exception("Exception")
		time.sleep(10)

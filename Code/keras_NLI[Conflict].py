#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun May 13 13:52:44 2018

Code to train an NLI model, heavily based upon the work of Stephen Merity
https://github.com/Smerity/keras_snli/blob/master/LICENSE

@author: rita
"""

from functools import reduce
import json
import os
import re
import tarfile
import tempfile

import pandas as pd
import numpy as np
np.random.seed(1337)


import keras
import keras.backend as K
from keras.callbacks import EarlyStopping, ModelCheckpoint
from keras.layers import merge, recurrent, Dense, Input, Dropout, TimeDistributed
from keras.layers.embeddings import Embedding
from keras.layers.normalization import BatchNormalization
from keras.layers.wrappers import Bidirectional
from keras.models import Model
from keras.models import load_model
from keras.preprocessing.sequence import pad_sequences
from keras.preprocessing.text import Tokenizer
from keras.regularizers import l2
from keras.utils import np_utils

model_path = '/Users/rita/Google Drive/DSR/DSR Project/Dataset/models'
data_path = '/Users/rita/Google Drive/DSR/DSR Project/Dataset/NLI/'

path1 = os.path.join(data_path, 'multinli_1.0/multinli_1.0_train.jsonl')
path2 = os.path.join(data_path, 'snli_1.0/snli_1.0_train.jsonl')




def extract_tokens_from_binary_parse(parse):
    return parse.replace('(', ' ').replace(')', ' ').replace('-LRB-', '(').replace('-RRB-', ')').split()


def yield_examples(path, skip_no_majority = True, limit = None):
    
    for i, line in enumerate(open(path)):
        if limit and i > limit:
            break
        
        data = json.loads(line)
        label = data['gold_label']
        s1 = ' '.join(extract_tokens_from_binary_parse(data['sentence1_binary_parse']))
        s2 = ' '.join(extract_tokens_from_binary_parse(data['sentence2_binary_parse']))
        if skip_no_majority and label == '-':
            continue
    
        yield (label, s1, s2)


def get_data(path, limit = None):
    
    raw_data = list(yield_examples(path = path, limit = limit))
    left = [s1 for _, s1, s2 in raw_data]
    right = [s2 for _, s1, s2 in raw_data]
    print(max(len(x.split()) for x in left))
    print(max(len(x.split()) for x in right))
    
    LABELS = {'contradiction': 0, 'neutral': 1, 'entailment': 2}
    Y = np.array([LABELS[l] for l, s1, s2 in raw_data])
    Y = np_utils.to_categorical(Y, len(LABELS))
    
    return left, right, Y


multinli = get_data(path1)
snli = get_data(path2)

all_premises = multinli[0] + snli[0]
all_hypothesis = multinli[1] + snli[1]

all_labels = pd.DataFrame(multinli[2])
all_labels = all_labels.append(pd.DataFrame(snli[2]))
all_labels = all_labels.as_matrix()


MAX_LEN = 50
all_texts = all_premises + all_hypothesis

# Save tokenizer
tokenizer = Tokenizer(lower=False, filters='')
tokenizer.fit_on_texts(all_texts)
#tokenizer.save(os.path.join(model_path, 'nli_tokenizer'))

all_premises = tokenizer.texts_to_sequences(all_premises)
all_hypothesis = tokenizer.texts_to_sequences(all_hypothesis)

all_premises = pad_sequences(all_premises, maxlen=MAX_LEN)
all_hypothesis = pad_sequences(all_hypothesis, maxlen=MAX_LEN)


VOCAB = len(tokenizer.word_counts) + 1
LABELS = {'contradiction': 0, 'neutral': 1, 'entailment': 2}
#RNN = recurrent.LSTM
#RNN = lambda *args, **kwargs: Bidirectional(recurrent.LSTM(*args, **kwargs))
#RNN = recurrent.GRU
#RNN = lambda *args, **kwargs: Bidirectional(recurrent.GRU(*args, **kwargs))
# Summation of word embeddings
RNN = None
LAYERS = 1
EMBED_HIDDEN_SIZE = 300
SENT_HIDDEN_SIZE = 300
BATCH_SIZE = 512
PATIENCE = 4 # 8
MAX_EPOCHS = 42
DP = 0.2
L2 = 4e-6
ACTIVATION = 'relu'
OPTIMIZER = 'rmsprop'
print('RNN / Embed / Sent = {}, {}, {}'.format(RNN, EMBED_HIDDEN_SIZE, SENT_HIDDEN_SIZE))

train_data = ((all_premises, all_hypothesis), all_labels)

print('Computing GloVe')
  
embeddings_index = {}
f = open(os.path.join(data_path, 'glove.840B.300d.txt'))
for line in f:
    values = line.split(' ')
    word = values[0]
    coefs = np.asarray(values[1:], dtype='float32')
    embeddings_index[word] = coefs
f.close()

# prepare embedding matrix
embedding_matrix = np.zeros((VOCAB, EMBED_HIDDEN_SIZE))
for word, i in tokenizer.word_index.items():
    embedding_vector = embeddings_index.get(word)
    if embedding_vector is not None:
        # words not found in embedding index will be all-zeros
        embedding_matrix[i] = embedding_vector
    else:
        print('Missing from GloVe: {}'.format(word))

#    print('Loading GloVe')
#    GLOVE_STORE = 'precomputed_glove.weights'
#    embedding_matrix = np.load(os.path.join(data_path,(GLOVE_STORE + '.npy')))

print('Total number of null word embeddings:')
print(np.sum(np.sum(embedding_matrix, axis=1) == 0))

embed = Embedding(VOCAB, EMBED_HIDDEN_SIZE, weights=[embedding_matrix], input_length=MAX_LEN, trainable=False)

rnn_kwargs = dict(output_dim=SENT_HIDDEN_SIZE, dropout_W=DP, dropout_U=DP)

SumEmbeddings = keras.layers.core.Lambda(lambda x: K.sum(x, axis=1), output_shape=(SENT_HIDDEN_SIZE, ))

translate = TimeDistributed(Dense(SENT_HIDDEN_SIZE, activation=ACTIVATION))

premise = Input(shape=(MAX_LEN,), dtype='int32')
hypothesis = Input(shape=(MAX_LEN,), dtype='int32')

prem = embed(premise)
hypo = embed(hypothesis)

prem = translate(prem)
hypo = translate(hypo)

if RNN and LAYERS > 1:
  for l in range(LAYERS - 1):
    rnn = RNN(return_sequences=True, **rnn_kwargs)
    prem = BatchNormalization()(rnn(prem))
    hypo = BatchNormalization()(rnn(hypo))
rnn = SumEmbeddings if not RNN else RNN(return_sequences=False, **rnn_kwargs)
prem = rnn(prem)
hypo = rnn(hypo)
prem = BatchNormalization()(prem)
hypo = BatchNormalization()(hypo)

joint = merge([prem, hypo], mode='concat')
joint = Dropout(DP)(joint)
for i in range(3):
  joint = Dense(2 * SENT_HIDDEN_SIZE, activation=ACTIVATION, W_regularizer=l2(L2) if L2 else None)(joint)
  joint = Dropout(DP)(joint)
  joint = BatchNormalization()(joint)

pred = Dense(len(LABELS), activation='softmax')(joint)

model = Model(input=[premise, hypothesis], output=pred)
model.compile(optimizer=OPTIMIZER, loss='categorical_crossentropy', metrics=['accuracy'])

model.summary()

print('Training')
_, tmpfn = tempfile.mkstemp()
# Save the best model during validation and bail out of training early if we're not improving
callbacks = [EarlyStopping(patience=PATIENCE), ModelCheckpoint(tmpfn, save_best_only=True, save_weights_only=True)]
model.fit([np.array(all_premises), np.array(all_hypothesis)], all_labels, batch_size=BATCH_SIZE, nb_epoch=MAX_EPOCHS, callbacks=callbacks)

# Restore the best found model during validation
model.save(os.path.join(model_path, 'NLI_nn.h5'))





# testing:
def prep_my_df():
    
    premises = [[' '.join(word_tokenize(b)) for b in sent_tokenize(a)] for a in df.text]
    hypothesis = premises[1:]
    return 
    

thisDoc = tokenizer.texts_to_sequences(words[8])
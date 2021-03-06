import hashlib
import json
from time import time
from urllib.parse import urlparse
from uuid import uuid4
from fastecdsa import curve, ecdsa, keys

import requests
from flask import Flask, jsonify, request

CHAIN_FILE = 'chain9.json'
NODES_FILE = 'nodes.json'

to_dict = lambda x: x.__dict__


def get_keypair():
    priv_key, pub_key = keys.gen_keypair(curve.P256)
    pub = f'{pub_key.x} {pub_key.y}'
    return pub, priv_key


def transactions_to_jsonSerializable(transactions_):
    transactions = []
    for transaction_ in transactions_:
        transaction = {}
        if transaction_.sender == '0':
            transaction['sender'] = transaction_.sender
        else:
            transaction['sender'] = transaction_.sender.address

        transaction['recipient'] = transaction_.recipient.address
        transaction['amount'] = transaction_.amount
        transaction['message'] = transaction_.message
        transaction['signature'] = transaction_.signature
        transactions.append(transaction)

    return transactions


def block_to_jsonSerializable(block_):
    block = {}
    block['index'] = block_['index']
    block['timestamp'] = block_['timestamp']
    block['proof'] = block_['proof']
    block['previous_hash'] = block_['previous_hash']
    block['transactions'] = transactions_to_jsonSerializable(
        block_['transactions']
    )
    return block


def chain_to_jsonSerializable(chain_):
    chain = []
    for block in chain_:
        chain.append(block_to_jsonSerializable(block))

    return chain


class AddressNotFound(Exception):
    pass


class Address():

    def __init__(self, address):
        self.address = address
        self.amount = 0


class Transaction():

    def __init__(self, sender, recipient, amount, signature=None, message=None):
        self.sender, self.senderIndex = sender
        self.recipient, self.recipientIndex = recipient
        self.amount = amount
        self.message = message
        self.signature = signature
        self.executed = False

    def is_valid(self):
        if (self.sender != '0' and self.sender.amount < self.amount) or (self.amount == 0):
            return False
        else:
            return True

    def is_signature_valid(self, transactions):
        if self.sender == '0':
            return True
        else:
            sigR, sigS = map(int, self.signature.split())
            senderX, senderY = map(int, self.sender.address.split())
            sender_transactions = len(transactions)
            msg_signed = f'{self.sender.address} {self.recipient.address} {self.message} {self.amount} {sender_transactions}'
            return ecdsa.verify((sigR, sigS), msg_signed, (senderX, senderY))


class Blockchain():

    def __init__(self):
        self.current_transactions = []
        self.addresses = []

        file_chain = open(CHAIN_FILE)
        self.chain = json.load(file_chain)
        for block in self.chain:
            transactions_ = []
            for transaction in block['transactions']:
                if transaction['sender'] == '0':
                    sender = '0'
                    senderIndex = None
                else:
                    sender, senderIndex = self.getOrCreateAddress(
                        transaction['sender']
                    )

                recipient, recipientIndex = self.getOrCreateAddress(
                    transaction['recipient']
                )

                transaction_ = Transaction(
                    (sender, senderIndex),
                    (recipient, recipientIndex),
                    transaction['amount'],
                    signature=transaction['signature']
                )

                sender_transactions = self.getTransactionsByAddress(sender)
                if not (transaction_.is_valid() and transaction_.is_signature_valid(sender_transactions)):
                    pass  # break the execution if transactions is not valid

                self.execute_transaction(transaction_)
                transactions_.append(transaction_)

            block['transactions'] = transactions_

        file_chain.close()

        file_nodes = open(NODES_FILE)
        self.nodes = set(json.load(file_nodes))
        file_nodes.close()

        # the genesis block
        if len(self.chain) == 0:
            self.new_transaction(
                sender='0',
                recipient=public_key,
                amount=1
            )

            self.new_block(previous_hash=1, proof=100)

    def update_chainFile(self):
        """
        Update the chain file
        """
        chain = chain_to_jsonSerializable(self.chain)
        file_chain = open(CHAIN_FILE, 'w')
        json_chain = json.dump(chain, file_chain)
        file_chain.close()

    def update_nodesFile(self):
        """
        Update the nodes file
        """
        file_nodes = open(NODES_FILE, 'w')
        json_nodes = json.dump(list(self.nodes), file_nodes)
        file_nodes.close()

    def new_block(self, proof, previous_hash=None):
        """
        Create a new block in the blockchain

        :param proof: <int> The proof given by the Proof of Work algorithm
        :param previous_hash: (optional) <str> Hash of the previous Block
        :return: <dict> New Block
        """
        if len(self.chain) != 0:
            index = len(self.chain)
        else:
            index = 0

        block = {
            'index': index,
            'timestamp': time(),
            'transactions': self.current_transactions,
            'proof': proof,
            'previous_hash': previous_hash or self.hash(self.chain[-1])
        }

        # clean the current transactions list
        self.current_transactions = []

        self.chain.append(block)
        self.update_chainFile()

        return block

    def get_address(self, address):
        """
        Return the wallet and the index in the self.addresses list

        :param address: <string> the address of the wallet
        :return: <Address> <int> the address and it index in the self.addresses
        """
        if address == '0':
            return address, None
        list_of_addresses = [ad.address for ad in self.addresses]
        for index, address_ in enumerate(list_of_addresses):
            if address == address_:
                return self.addresses[index], index

        raise AddressNotFound()

    def getOrCreateAddress(self, address):
        """
        Return the wallet (or create it) and the index in the self.addresses list

        :param address: <string> the address of the wallet
        :return: <Address> <int> the address and it index in the self.addresses
        """
        try:
            return self.get_address(address)
        except AddressNotFound:
            address = Address(address)
            self.addresses.append(address)
            index = len(self.addresses) - 1
            return address, index

    def new_transaction(self, sender, recipient, amount, signature=None, message=None):
        """
        Creates a new transaction to go into the next mined Block

        :param sender: <str> Address of the Sender
        :param recipient: <str> Address of the Recipient
        :param amount: <int> Amount
        :param signature: <int> signature that confirm the owner's wallet
        :param message: <str> optional. message of the transaction
        :return: <int> The index of the Block that will hold this transaction
        """
        try:
            sender, senderIndex = self.get_address(sender)
        except AddressNotFound:
            return None

        recipient, recipientIndex = self.getOrCreateAddress(recipient)

        transaction = Transaction(
            (sender, senderIndex),
            (recipient, recipientIndex),
            amount,
            signature,
            message
        )

        sender_transactions = self.getTransactionsByAddress(sender)
        if not (transaction.is_valid() and transaction.is_signature_valid(sender_transactions)):
            return None  # stop execution if transactions is not valid

        self.execute_transaction(transaction)
        self.current_transactions.append(transaction)

        if len(self.chain) == 0:
            return 0
        else:
            return self.last_block['index'] + 1

    @property
    def last_block(self):
        return self.chain[-1]

    @staticmethod
    def hash(block):
        """
        Creates a SHA-256 hash of a Block

        :param block: <dict> Block
        :return: <str> hash
        """

        block_string = json.dumps(block, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def proof_of_work(self, last_proof):
        """
        Simple Proof of Work Algorithm:
         - Find a number p' such that hash(pp') contains leading 4 zeroes, where p is the previous p'
         - p is the previous proof, and p' is the new proof

        :param last_proof: <int>
        :return: <int>
        """

        proof = 0
        while self.valid_proof(last_proof, proof) is False:
            proof += 1

        return proof

    @staticmethod
    def valid_proof(last_proof, proof):
        """
        Validates the Proof: Does hash(last_proof, proof) contain 4 leading zeroes?

        :param last_proof: <int> Previous Proof
        :param proof: <int> Current Proof
        :return: <bool> true if correct, false if not
        """

        guess = f'{last_proof}{proof}'.encode()
        guess_hash = hashlib.sha256(guess).hexdigest()
        return guess_hash[:4] == '0000'

    def register_node(self, address):
        """
        Add a new node to the list of nodes

        :param address: <str> Address of the node. Eg. 'https://192.168.0.5:5000'
        :return: None
        """

        parsed_url = urlparse(address)
        self.nodes.add(parsed_url.netloc)
        self.update_nodesFile()

    def valid_chain(self, chain):
        """
        Determine if a given blockchain is valid

        :param chain: <list> a blockchain
        :return: <bool> true if valid, false if not
        """

        last_block = chain[0]
        current_index = 1

        while current_index < len(chain):
            block = chain[current_index]
            print(f'{last_block}')
            print(f'{block}')
            print('\n------------\n')
            # check that the hash of the block is correct
            if block['previous_hash'] != self.hash(last_block):
                return False

            # check that the Proof of Work is correct
            if not self.valid_proof(last_block['proof'], block['proof']):
                return False

            if block['timestamp'] < last_block['timestamp']:
                return False

            last_block = block
            current_index += 1

        return True

    def resolve_conflicts(self):
        """
        This is our Consensus Algorithm, it resolves conflicts
        by replacing our chain with the longest one in the network

        :return: <bool> True if our Chain was replaced, False if not
        """

        neighbours = self.nodes
        new_chain = None
        invalid_chains = []

        if len(neighbours) == 0:
            print('this node is alone')
            return False, invalid_chains

        # we're only looking for chains longer than ours
        max_length = len(self.chain)

        # grab and verify the chains from all the nodes in our network
        for node in neighbours:
            response = requests.get(f'http://{node}/chain')

            if response.status_code == 200:
                length = response.json()['length']
                chain = response.json()['chain']

                # check if the length is longer and the chain is valid
                if length > max_length and self.valid_chain(chain):
                    max_length = length
                    new_chain = chain
                elif not (length < max_length and self.valid_chain(chain)):
                    invalid_chains.append(node)

            if new_chain:
                self.chain = new_chain
                return True, invalid_chains

            return False, invalid_chains

    def execute_transaction(self, transaction):
        """
        Execute the transaction received

        :param transaction: <Transaction> the transaction
        """
        if transaction.sender != '0':
            self.addresses[
                transaction.senderIndex].amount -= transaction.amount

        self.addresses[transaction.recipientIndex].amount += transaction.amount
        transaction.executed = True

    def getTransactionsByAddress(self, address):
        """
        Return all transaction containing the address received, and the future_transactions to the next block

        :param address: <string> or <Address> the address of the wallet
        :return: List<Transactions> transactions containing the address or None,None when the wallet doesn't exist
        """
        try:
            address, addressIndex = self.get_address(address)
        except AddressNotFound:
            if not isinstance(address, Address):
                return None, None

        transactions = []
        for block in self.chain:
            for t in block['transactions']:
                try:
                    if address in (t.sender, t.recipient):
                        transactions.append(t)
                except:
                    if address in (t['sender'], t['recipient']):
                        transactions.append(t)

        future_transactions = []
        for t in self.current_transactions:
            if address in (t.sender, t.recipient):
                future_transactions.append(t)

        return transactions, future_transactions

    def get_balance(self, address):
        """
        Return the balance of the wallet

        :param address: <string> the address of the wallet
        :return: <int> the amont of the wallet
        """
        try:
            address, addressIndex = self.get_address(address)
        except AddressNotFound:
            return None, None

        return address.amount


app = Flask(__name__)

option = input('do you already have your public/private keys? [(Y)es/(N)o]: ')
if option == 'Y':
    public_key = input('enter your public key: ')
else:
    public_key, private_key = get_keypair()
    print(f'your public key is: {public_key}')
    print(f'your private key is: {private_key}\nkeep it in secret please')

blockchain = Blockchain()

@app.route('/mine', methods=['GET'])
def mine():
    # We run the proof of work algorithm to get the next proof
    last_block = blockchain.last_block
    last_block = block_to_jsonSerializable(last_block)

    last_proof = last_block['proof']
    proof = blockchain.proof_of_work(last_proof)

    # we must receive a reward for finding the proof
    # the sender is '0' to signify that this node has mined a new coin

    blockchain.new_transaction(
        sender='0',
        recipient=public_key,
        amount=1
    )

    # forge the new Block by adding it to the chain
    previous_hash = blockchain.hash(last_block)
    block = blockchain.new_block(proof, previous_hash)
    transactions = transactions_to_jsonSerializable(block['transactions'])

    response = {
        'message': 'New block forged',
        'index': block['index'],
        'transactions': transactions,
        'proof': block['proof'],
        'previous_hash': block['previous_hash']
    }

    return jsonify(response), 200


@app.route('/transactions/new', methods=['POST'])
def new_transaction():
    values = request.get_json()

    required = ['sender', 'recipient', 'amount', 'signature']
    if not all(k in values for k in required):
        return 'Missing value(s): %s' % ([k for k in required if not k in values]), 400

    if 'message' in values:
        index = blockchain.new_transaction(
            values['sender'],
            values['recipient'],
            values['amount'],
            signature=values['signature'],
            message=values['message']
        )
    else:
        index = blockchain.new_transaction(
            values['sender'],
            values['recipient'],
            values['amount'],
            signature=values['signature']
        )

    if index == None:
        response = {'message': 'Invalid transaction'}
    else:
        response = {'message': f'Transaction added to the Block {index}'}

    return jsonify(response), 201


@app.route('/chain', methods=['GET'])
def full_chain():
    chain = chain_to_jsonSerializable(blockchain.chain)
    response = {
        'chain': chain,
        'length': len(blockchain.chain)
    }
    return jsonify(response), 200


@app.route('/node/register', methods=['POST'])
def register_nodes():
    values = request.get_json()

    nodes = values.get('nodes')
    if nodes is None:
        return 'Error: please supply a valid list of nodes', 400

    for node in nodes:
        blockchain.register_node(node)

    response = {
        'message': 'new nodes have been added',
        'total_nodes': list(blockchain.nodes)
    }

    return jsonify(response), 201


@app.route('/node/resolve', methods=['GET'])
def consensus():
    replaced, invalid_chains = blockchain.resolve_conflicts()
    print(f'invalid chains: {invalid_chains}')
    chain = chain_to_jsonSerializable(blockchain.chain)

    if replaced:
        response = {
            'message': 'our chain was replaced',
            'new_chain': chain
        }
    else:
        response = {
            'message': 'our chain is authoritative',
            'chain': chain
        }

    return jsonify(response), 200


@app.route('/transactions/wallet', methods=['POST'])
def get_transactions():
    values = request.get_json()

    if not 'address' in values:
        return 'Error: Missing address value', 400

    transactions, future_transactions = blockchain.getTransactionsByAddress(
        values['address']
    )

    if transactions == None:
        return "Error: this address doesn't exist", 400

    transactions = transactions_to_jsonSerializable(transactions)
    future_transactions = transactions_to_jsonSerializable(future_transactions)

    actual_balance = blockchain.get_balance(values['address'])

    response = {
        'transactions': transactions,
        'future_transactions': future_transactions,
        'balance': actual_balance
    }

    return jsonify(response),  200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

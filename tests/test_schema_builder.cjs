const assert = require('node:assert/strict');
const test = require('node:test');

const editor = require('../ui/schema-builder.js');

test('round-trips nested JSON Schema, required fields, enums, and array items', () => {
    const schema = {
        type: 'object',
        title: 'Review',
        properties: {
            status: {
                type: 'string',
                description: 'Current state',
                enum: ['ready', 'hold'],
            },
            records: {
                type: 'array',
                items: {
                    type: 'object',
                    properties: {
                        count: { type: 'integer' },
                        enabled: { type: 'boolean' },
                    },
                    required: ['count'],
                    additionalProperties: false,
                },
            },
        },
        required: ['status'],
        additionalProperties: false,
    };

    const tree = editor.fromSchema(schema);

    assert.deepEqual(editor.toSchema(tree), schema);
    assert.equal(editor.validate(tree), '');
    assert.deepEqual(editor.sample(tree), {
        status: 'ready',
        records: [{ count: 0, enabled: false }],
    });
});

test('rejects duplicate field names and empty object or array item structures', () => {
    const duplicate = editor.fromSchema({
        type: 'object',
        properties: { first: { type: 'string' }, second: { type: 'number' } },
    });
    duplicate.children[1].key = 'first';
    assert.match(editor.validate(duplicate), /不能重复/);

    const emptyObject = editor.fromSchema({
        type: 'object',
        properties: { details: { type: 'object', properties: {} } },
    });
    assert.match(editor.validate(emptyObject), /至少要添加一个子字段/);

    const emptyArrayItem = editor.fromSchema({
        type: 'object',
        properties: {
            records: { type: 'array', items: { type: 'object', properties: {} } },
        },
    });
    assert.match(editor.validate(emptyArrayItem), /至少要添加一个子字段/);
});

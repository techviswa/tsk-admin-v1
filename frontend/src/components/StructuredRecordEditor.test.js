import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import StructuredRecordEditor from './StructuredRecordEditor';

let element, root, saved;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; element = document.createElement('div'); root = createRoot(element); saved = null; });
afterEach(async () => { await act(async () => root.unmount()); });
function Editor({ initial = '[]' }) {
  const [text, setText] = useState(initial);
  return <StructuredRecordEditor label="Item" text={text} template={{ name: '', quantity: 1, price: 0 }} onChange={value => { saved = JSON.parse(value); setText(value); }} />;
}
const change = async (input, value) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
test('structured editing preserves comma-containing names, zero values and additional saved fields', async () => {
  await act(async () => root.render(<Editor initial='[{"name":"Tea, large","quantity":1,"price":0,"reference":"keep-me"}]' />));
  await change(element.querySelector('[aria-label="Quantity"]'), '2');
  expect(saved[0]).toEqual({ name: 'Tea, large', quantity: 2, price: 0, reference: 'keep-me' });
  await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === 'Add item').click());
  expect(saved).toHaveLength(2);
  expect(saved[1]).toEqual({ name: '', quantity: 1, price: 0 });
});
test('malformed saved collections remain preserved and cannot silently become empty rows', async () => {
  await act(async () => root.render(<Editor initial='{broken' />));
  expect(element.querySelector('[role="alert"]').textContent).toContain('preserved');
  expect(saved).toBeNull();
  expect(element.querySelectorAll('input')).toHaveLength(0);
});
